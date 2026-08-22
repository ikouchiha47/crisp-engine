Feature: Episode dedup correctness
  Deleting an episode and later re-saving identical content must not be
  silently swallowed by a stale hash-cache entry. (Audit finding #2,
  store.py:548 — delete_episode's hash_cache filter compares content-hash
  keys against an episode ID, which never matches, so the stale mapping
  survives the delete.)

  Scenario: Re-saving identical content after deleting the original episode
    Given a memory store
    And an episode "ep1" with content "the exact same content" is saved
    When episode "ep1" is deleted
    And a new episode "ep2" with content "the exact same content" is saved
    Then episode "ep2" must exist in the store
