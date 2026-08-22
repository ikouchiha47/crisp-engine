Feature: File state lookups use a consistent path identity
  save_episode must read and write file_states using the same hashed-path
  key that get_file_state/set_file_state/_get_episode_id_for_file already
  use, or a file-sourced episode's change-detection silently never matches
  anything ever written by those other methods. (Audit finding #1.)

  Scenario: A file-sourced episode's state is visible via get_file_state
    Given a memory store
    And a file-sourced episode "fep1" for path "/repo/src/app.py" with source hash "abc123" is saved
    When I ask for the file state of "/repo/src/app.py"
    Then the file state must be "abc123"

  Scenario: Saving the same file content twice does not create a second episode
    Given a memory store
    And a file-sourced episode "fep1" for path "/repo/src/app.py" with source hash "abc123" is saved
    And a file-sourced episode "fep2" for path "/repo/src/app.py" with source hash "abc123" is saved
    Then only one episode must exist for path "/repo/src/app.py"
