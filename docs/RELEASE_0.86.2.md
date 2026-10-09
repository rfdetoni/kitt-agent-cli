# Agent CLI 0.86.2

Completion and contract-validation prompts now use KAP/1 structured content consistently with contract v4. A failed validation example specifies each issue as an object with severity, location, problem and fix; JSON inside TEXT content is no longer requested. Review and completion remain evidence-based and fail closed.

The read-only validator receives the current item's planned files and recorded changed files as explicit inputs, together with the original selected files. This supplies current workspace data when no mutation snapshot was recorded. File scopes and read-only capabilities remain enforced.

Failover excludes alternatives whose origins are not trusted for their provider. A configured fallback such as port 3001 cannot replace a connection failure on the selected port 3000 with an authorization error. Trust must still be granted by the existing explicit provider setup/binding flow. Primary authorization errors are unchanged.

process.run and process.start reject malformed argv before policy evaluation with a precise input error, including nested lists. Autonomous mode still enforces sandbox, shell, path and capability restrictions.

Validation extends existing regressions for all three provider dispatch paths, typed report examples, explicit validation inputs and malformed process vectors. Compatible with Protocol 0.11.0 and Reverse Proxy 5.1.1. No dependency or wire-version change.
