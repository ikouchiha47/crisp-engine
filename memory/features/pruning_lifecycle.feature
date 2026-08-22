Feature: Decay-based pruning actually archives and deletes
  An episode old and low-value enough to qualify must actually move through
  the archive/delete lifecycle, not just report zero counts every time.
  (Audit findings #4 and #16 — prune.py's archive_low_value gates archival on
  parent_id, which is only ever set by reflector consolidation of large
  sessions, so a lone old low-importance episode is never archived; and
  delete_ancient_archives only deletes files already in the archived/
  subdirectory whose file mtime is old, so a live episode is never deleted
  directly regardless of its own age.)

  Scenario: A single old, low-importance, unconsolidated episode gets archived
    Given a memory store
    And an episode "old_lonely" with timestamp 400 days ago is saved
    When pruning is run
    Then episode "old_lonely" must be archived

  Scenario: An already-archived episode with an old archive file gets deleted
    Given a memory store
    And an episode "ancient" with timestamp 400 days ago is saved
    And "ancient" is already archived with an archive file mtime 800 days old
    When pruning is run
    Then episode "ancient" must be permanently deleted
