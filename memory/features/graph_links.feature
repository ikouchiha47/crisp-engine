Feature: Graph-expanded search does not crash
  A-MEM style links between episodes must be usable by the actual retrieval
  and pruning code paths, not just readable directly off the store. (Audit
  finding #3 — store.py's get_links() returns tuples, but retrieve.py and
  prune.py index the results as dicts, e.g. link["target_id"], raising
  TypeError the moment a link exists and graph-expanded search runs.)

  Scenario: Searching after a link exists between two episodes does not raise
    Given a memory store
    And an episode "ep1" with content "authentication flow" is saved
    And an episode "ep2" with content "token refresh logic" is saved
    And "ep1" is linked to "ep2"
    When a graph-expanded search for "authentication" is run
    Then the search must complete without raising an exception

  Scenario: Pruning does not crash once a link exists
    Given a memory store
    And an episode "ep1" with content "authentication flow" is saved
    And an episode "ep2" with content "token refresh logic" is saved
    And "ep1" is linked to "ep2"
    When pruning is run
    Then pruning must complete without raising an exception
