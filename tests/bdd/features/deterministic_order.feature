Feature: Deterministic output with concurrent validation
  Scenario: Stream output remains ordered even when tasks finish out of order
    Given markdown fixtures with delayed and failing diagram checks
    When I validate the fixtures with nixie
    Then diagram markers are emitted in deterministic file and diagram order
    And the failing diagram error is reported on stderr
