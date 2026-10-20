# KITT Agent CLI 0.88.19

## Fail-closed Agent v5 host tools
Agent-loop dispatch now trusts only complete explicit function-action envelopes or the established six-field JSON Agent v5 contract, including native function calls bridged by `kitt_reverse_proxy`. Ordinary prose, markdown examples and malformed JSON cannot become host tool invocations. The current host validates each action and its capability grants; neither tool names nor args are inferred from natural language.

`repo.batch_read` is the established bounded parallel read operation. No generic parallel writes/process execution were added.

### Validation
Regression tests cover explicit compact and native bridge frames versus prose, multi-frame text and duplicate-key argument injection. Run release-critical CI before integration. No live authenticated WebChat performance run was available.
