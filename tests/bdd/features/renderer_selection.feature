Feature: Renderer selection
  nixie chooses between merman-cli and the Node-based mermaid-cli.

  Scenario: Auto mode prefers merman-cli when it is installed
    Given merman-cli is installed and a Node environment is installed
    And a Markdown fixture containing one valid diagram
    When I validate the fixture with nixie in auto renderer mode
    Then the diagram is rendered with merman-cli
    And no Puppeteer configuration file is created

  Scenario: Auto mode falls back to mmdc when merman-cli is absent
    Given merman-cli is not installed and a Node environment is installed
    And a Markdown fixture containing one valid diagram
    When I validate the fixture with nixie in auto renderer mode
    Then the diagram is rendered with the Node-based mermaid-cli

  Scenario: Forcing merman without merman-cli fails clearly
    Given merman-cli is not installed
    And a Markdown fixture containing one valid diagram
    When I validate the fixture with nixie forcing the merman renderer
    Then validation fails before any diagram is rendered
    And the error names merman-cli and how to install it

  Scenario: Forcing mmdc preserves the legacy pipeline
    Given merman-cli is installed and a Node environment is installed
    And a Markdown fixture containing one valid diagram
    When I validate the fixture with nixie forcing the mmdc renderer
    Then the diagram is rendered with the Node-based mermaid-cli
