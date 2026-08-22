Feature: Shared JSON state files survive corruption and concurrent writes
  Reproduced for real on this project's own hashes.json during development —
  two interleaved writes fused into one file, crashing every subsequent
  hook. Loads must tolerate corruption instead of crashing the whole store,
  and writes must be atomic (temp file + rename) so this can't recur.

  Scenario: A corrupted hash cache file does not crash store initialization
    Given a memory store directory
    And its hashes.json contains two concatenated JSON objects like a torn write
    When a MemoryStore is opened against that directory
    Then it must not raise an exception
    And the hash cache must contain the first object's entries

  Scenario: Saving the hash cache never leaves a torn write on disk
    Given a memory store
    When 50 episodes with distinct content are saved in sequence
    Then hashes.json must be valid JSON after every single save
