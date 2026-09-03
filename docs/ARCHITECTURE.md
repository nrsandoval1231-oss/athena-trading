# Architecture

Athena Trading is a domain client of Athena Core.

```text
Market data -> Strategy -> Risk -> Position management -> Trading outcome
                    |                         |
                    +---- Athena adapter -----+
                              |
                         Athena Core
                       Arena / Gauntlet
```

The dependency direction is one-way: Athena Trading may depend on Athena contracts; Athena Core must never depend on trading primitives.
