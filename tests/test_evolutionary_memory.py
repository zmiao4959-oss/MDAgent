import time

from miniclaw.memory.evolution import EvolutionaryMemory


def test_decay_is_incremental_not_reapplied_from_creation_time():
    memory = EvolutionaryMemory()
    entry = memory.add("The project uses incremental memory decay")
    entry.timestamp = time.time() - 30 * 86400
    entry.last_decay_at = entry.timestamp

    memory._apply_decay()
    first_weight = entry.weight
    memory._apply_decay()

    assert 0.49 <= first_weight <= 0.51
    assert abs(entry.weight - first_weight) < 0.001


def test_recall_counts_evidence_for_consolidation():
    memory = EvolutionaryMemory()
    entry = memory.add("LAMMPS input files should be checked before execution")

    assert memory.recall("check LAMMPS input file") == [entry]
    assert entry.references == 1
