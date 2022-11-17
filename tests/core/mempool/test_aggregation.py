from typing import Tuple, List

import pytest
from blspy import G2Element

from chia.clvm.spend_sim import SimClient, SpendSim
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_spend import CoinSpend
from chia.types.spend_bundle import SpendBundle
from chia.util.errors import Err
from chia.util.ints import uint64
from chia.wallet.lineage_proof import LineageProof
from chia.wallet.puzzles import singleton_top_layer_v1_1 as singleton_top_layer
from chia.wallet.puzzles.load_clvm import load_clvm

SINGLETON_MOD = load_clvm("singleton_top_layer_v1_1.clvm")
LAUNCHER_PUZZLE = load_clvm("singleton_launcher.clvm")
COUNTER_PUZZLE = Program.fromhex(
    "ff02ffff01ff04ffff04ff18ffff04ffff02ff16ffff04ff02ffff04ff05ffff04ffff02ff2effff04ff02ffff04ffff02ff3effff04ff02ffff04ff17ffff04ff0bff8080808080ff80808080ffff04ffff02ff2effff04ff02ffff04ff05ff80808080ff808080808080ffff01ff01808080ff8080ffff04ffff01ffffff0233ff04ff0101ffff02ff02ffff03ff05ffff01ff02ff1affff04ff02ffff04ff0dffff04ffff0bff12ffff0bff2cff1480ffff0bff12ffff0bff12ffff0bff2cff3c80ff0980ffff0bff12ff0bffff0bff2cff8080808080ff8080808080ffff010b80ff0180ffff0bff12ffff0bff2cff1080ffff0bff12ffff0bff12ffff0bff2cff3c80ff0580ffff0bff12ffff02ff1affff04ff02ffff04ff07ffff04ffff0bff2cff2c80ff8080808080ffff0bff2cff8080808080ffff02ffff03ffff07ff0580ffff01ff0bffff0102ffff02ff2effff04ff02ffff04ff09ff80808080ffff02ff2effff04ff02ffff04ff0dff8080808080ffff01ff0bffff0101ff058080ff0180ff02ffff03ff05ffff01ff02ff3effff04ff02ffff04ff0dffff04ffff10ff0bff0980ff8080808080ffff010b80ff0180ff018080"
)
COUNTER_PUZZLE_NOAGG = Program.fromhex(
    "ff02ffff01ff04ffff04ff18ffff04ffff02ff2effff04ff02ffff04ff05ffff04ffff02ff3effff04ff02ffff04ffff10ff17ff0b80ff80808080ffff04ffff02ff3effff04ff02ffff04ff05ff80808080ff808080808080ffff01ff01808080ff8080ffff04ffff01ffffff0233ff0401ffff0102ffff02ffff03ff05ffff01ff02ff16ffff04ff02ffff04ff0dffff04ffff0bff1affff0bff1cff1480ffff0bff1affff0bff1affff0bff1cff1280ff0980ffff0bff1aff0bffff0bff1cff8080808080ff8080808080ffff010b80ff0180ffff0bff1affff0bff1cff1080ffff0bff1affff0bff1affff0bff1cff1280ff0580ffff0bff1affff02ff16ffff04ff02ffff04ff07ffff04ffff0bff1cff1c80ff8080808080ffff0bff1cff8080808080ff02ffff03ffff07ff0580ffff01ff0bffff0102ffff02ff3effff04ff02ffff04ff09ff80808080ffff02ff3effff04ff02ffff04ff0dff8080808080ffff01ff0bffff0101ff058080ff0180ff018080"
)

open_puzzle = Program.to(1)


async def assert_puzzle_hash(sim_client, launcher_id, value, puzzle=COUNTER_PUZZLE) -> None:
    puzzle_reveal: Program = singleton_top_layer.puzzle_for_singleton(
        launcher_id,
        puzzle.curry(puzzle.get_tree_hash(), value),
    )
    singleton_coin: Coin = (
        await sim_client.get_coin_records_by_puzzle_hash(puzzle_reveal.get_tree_hash(), include_spent_coins=False)
    )[-1].coin
    assert bytes32(singleton_coin.puzzle_hash) == puzzle_reveal.get_tree_hash()


async def make_bundle(
    sim_client, launcher_id, coinsol, solution, value, fee=0, fee_coin=None, puzzle=COUNTER_PUZZLE
) -> SpendBundle:
    latest_puzzle_reveal: Program = singleton_top_layer.puzzle_for_singleton(
        launcher_id,
        puzzle.curry(puzzle.get_tree_hash(), value),
    )
    lineage_proof: LineageProof = singleton_top_layer.lineage_proof_for_coinsol(coinsol)
    full_solution: Program = singleton_top_layer.solution_for_singleton(
        lineage_proof,
        uint64(1),
        Program.to([solution]),
    )

    latest_singleton: Coin = (
        await sim_client.get_coin_records_by_puzzle_hash(
            latest_puzzle_reveal.get_tree_hash(), include_spent_coins=False
        )
    )[0].coin
    spend = CoinSpend(
        latest_singleton,
        latest_puzzle_reveal,
        full_solution,
    )
    if fee > 0 and fee_coin is not None:
        fee_coin = fee_coin.coin
        fee_spend = CoinSpend(
            fee_coin,
            open_puzzle,
            Program.to([[51, open_puzzle.get_tree_hash(), fee_coin.amount - fee]]),
        )
        return SpendBundle([spend, fee_spend], G2Element())
    return SpendBundle([spend], G2Element())


@pytest.mark.asyncio
async def test_agg_singleton_spend(setup_sim: Tuple[SpendSim, SimClient]) -> None:
    sim, sim_client = setup_sim
    try:
        counter = COUNTER_PUZZLE.curry(COUNTER_PUZZLE.get_tree_hash(), 0)
        # make some change
        for _ in range(20):
            await sim.farm_block(open_puzzle.get_tree_hash())
        starting_coins = await sim_client.get_coin_records_by_puzzle_hash(open_puzzle.get_tree_hash())
        starting_coin = starting_coins[0].coin
        fee_coins = starting_coins[1:]
        conditions, launcher_coinsol = singleton_top_layer.launch_conditions_and_coinsol(  # noqa
            starting_coin, counter, [], uint64(1)
        )
        starting_coinsol = CoinSpend(
            starting_coin,
            open_puzzle,
            Program.to(conditions),
        )
        assert len(launcher_coinsol.additions()) == 1
        singleton_eve: Coin = launcher_coinsol.additions()[0]
        launcher_coin: Coin = singleton_top_layer.generate_launcher_coin(
            starting_coin,
            uint64(1),
        )
        lineage_proof: LineageProof = singleton_top_layer.lineage_proof_for_coinsol(launcher_coinsol)

        launcher_id: bytes32 = launcher_coin.name()
        puzzle_reveal: Program = singleton_top_layer.puzzle_for_singleton(
            launcher_id,
            counter,
        )
        inner_solution: Program = Program.to([[1000]])
        full_solution: Program = singleton_top_layer.solution_for_singleton(
            lineage_proof,
            uint64(1),
            inner_solution,
        )

        singleton_eve_coinsol = CoinSpend(
            singleton_eve,
            puzzle_reveal,
            full_solution,
        )

        bundle: SpendBundle = SpendBundle([singleton_eve_coinsol, starting_coinsol, launcher_coinsol], G2Element())
        result, error = await sim_client.push_tx(bundle)
        await sim.farm_block(open_puzzle.get_tree_hash())
        await sim.farm_block(open_puzzle.get_tree_hash())

        await assert_puzzle_hash(sim_client, launcher_id, 1000)

        result, error = await sim_client.push_tx(
            await make_bundle(sim_client, launcher_id, singleton_eve_coinsol, [1000], 1000)
        )
        assert error is None
        # no fee, raise error
        result, error = await sim_client.push_tx(
            await make_bundle(sim_client, launcher_id, singleton_eve_coinsol, [2000], 1000)
        )
        assert error == Err.MEMPOOL_CONFLICT
        # fee too low
        result, error = await sim_client.push_tx(
            await make_bundle(
                sim_client, launcher_id, singleton_eve_coinsol, [2000], 1000, fee=300000, fee_coin=fee_coins.pop()
            )
        )
        assert error == Err.MEMPOOL_CONFLICT
        result, error = await sim_client.push_tx(
            await make_bundle(
                sim_client,
                launcher_id,
                singleton_eve_coinsol,
                [2000],
                1000,
                fee=13000000,
                fee_coin=fee_coins.pop(),
            )
        )
        assert error is None
        singleton_eve_coinsol = (
            await make_bundle(sim_client, launcher_id, singleton_eve_coinsol, [2000], 1000)
        ).coin_spends[0]
        await sim.farm_block(open_puzzle.get_tree_hash())
        await sim.farm_block(open_puzzle.get_tree_hash())
        fee_counts = list(range(20))
        await assert_puzzle_hash(sim_client, launcher_id, 4000)
        multi_bundle_spend = SpendBundle.aggregate(
            [
                await make_bundle(
                    sim_client,
                    launcher_id,
                    singleton_eve_coinsol,
                    [3000],
                    4000,
                    fee=13000000,
                    fee_coin=fee_coins.pop(),
                ),
                await make_bundle(
                    sim_client,
                    launcher_id,
                    singleton_eve_coinsol,
                    [3000],
                    4000,
                    fee=13000000,
                    fee_coin=fee_coins.pop(),
                ),
            ]
        )
        result, error = await sim_client.push_tx(multi_bundle_spend)
        assert error == Err.DOUBLE_SPEND

        fee_counts = list(range(20))
        result, error = await sim_client.push_tx(
            await make_bundle(
                sim_client,
                launcher_id,
                singleton_eve_coinsol,
                [3000],
                4000,
                fee=11000000,
                fee_coin=fee_coins.pop(),
            )
        )
        assert error is None
        result, error = await sim_client.push_tx(
            await make_bundle(
                sim_client, launcher_id, singleton_eve_coinsol, [4000], 4000, fee=11000000, fee_coin=fee_coins.pop()
            )
        )
        assert error is None
        spend = await sim_client.get_all_mempool_items()
        await sim.farm_block(open_puzzle.get_tree_hash())
        await assert_puzzle_hash(sim_client, launcher_id, 11000)
        #

    finally:
        await sim.close()


@pytest.mark.asyncio
async def test_agg_singleton_spend_on_unsupported_singleton(setup_sim: Tuple[SpendSim, SimClient]) -> None:
    sim, sim_client = setup_sim
    try:
        counter = COUNTER_PUZZLE_NOAGG.curry(COUNTER_PUZZLE_NOAGG.get_tree_hash(), 0)
        # make some change
        for _ in range(20):
            await sim.farm_block(open_puzzle.get_tree_hash())
        starting_coins = await sim_client.get_coin_records_by_puzzle_hash(open_puzzle.get_tree_hash())
        starting_coin = starting_coins[0].coin
        fee_coins = starting_coins[1:]
        conditions, launcher_coinsol = singleton_top_layer.launch_conditions_and_coinsol(  # noqa
            starting_coin, counter, [], uint64(1)
        )
        starting_coinsol = CoinSpend(
            starting_coin,
            open_puzzle,
            Program.to(conditions),
        )
        assert len(launcher_coinsol.additions()) == 1
        singleton_eve: Coin = launcher_coinsol.additions()[0]
        launcher_coin: Coin = singleton_top_layer.generate_launcher_coin(
            starting_coin,
            uint64(1),
        )
        lineage_proof: LineageProof = singleton_top_layer.lineage_proof_for_coinsol(launcher_coinsol)

        launcher_id: bytes32 = launcher_coin.name()
        puzzle_reveal: Program = singleton_top_layer.puzzle_for_singleton(
            launcher_id,
            counter,
        )
        inner_solution: Program = Program.to([1000])
        full_solution: Program = singleton_top_layer.solution_for_singleton(
            lineage_proof,
            uint64(1),
            inner_solution,
        )

        singleton_eve_coinsol = CoinSpend(
            singleton_eve,
            puzzle_reveal,
            full_solution,
        )

        bundle: SpendBundle = SpendBundle([singleton_eve_coinsol, starting_coinsol, launcher_coinsol], G2Element())
        result, error = await sim_client.push_tx(bundle)
        await sim.farm_block(open_puzzle.get_tree_hash())
        await sim.farm_block(open_puzzle.get_tree_hash())

        await assert_puzzle_hash(sim_client, launcher_id, 1000, puzzle=COUNTER_PUZZLE_NOAGG)

        result, error = await sim_client.push_tx(
            await make_bundle(sim_client, launcher_id, singleton_eve_coinsol, 1000, 1000, puzzle=COUNTER_PUZZLE_NOAGG)
        )
        assert error is None
        result, error = await sim_client.push_tx(
            await make_bundle(
                sim_client,
                launcher_id,
                singleton_eve_coinsol,
                2000,
                1000,
                fee=13000000,
                fee_coin=fee_coins.pop(),
                puzzle=COUNTER_PUZZLE_NOAGG,
            )
        )
        assert error is Err.INVALID_SINGLETON_TO_AGGREGATE
        await assert_puzzle_hash(sim_client, launcher_id, 1000, puzzle=COUNTER_PUZZLE_NOAGG)
        await sim.farm_block(open_puzzle.get_tree_hash())
        await assert_puzzle_hash(sim_client, launcher_id, 2000, puzzle=COUNTER_PUZZLE_NOAGG)
    finally:
        await sim.close()
