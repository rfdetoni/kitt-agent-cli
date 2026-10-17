# KITT RE Clean-Room workflow (strict mode)

This flow deliberately refuses to call an implementer before a separately reviewed black-box API contract exists. It does not use prompts as an access-control mechanism.

1. Run the Spec Extractor in an analyst-only environment. Use public API signatures and externally observable return values/side effects. Do not transfer binaries, disassemblies, raw stdout, history, memory, artifact IDs, or internal Windows source to the implementer.
2. Save only the contract in the KITT Clean-Room schema. Validate with:
   kitt re cleanroom validate /path/to/reviewed-spec.json
3. An independent reviewer must examine provenance, leakage and compatibility with project contribution rules. Seal with:
   kitt re cleanroom seal /path/to/reviewed-spec.json --reviewer REVIEWER_ID
   The result is SEALED_NOT_EXECUTABLE, not approval of a child.
4. Record the returned contract_sha256 independently of the seal directory. Verify using:
   kitt re cleanroom verify RUN_ID --sha256 SHA256
5. An independent reviewer must sign the EXACT sealed contract.json with a private Ed25519 key never exposed to KITT or the analysis machine. Example on the independent reviewer's machine: openssl pkeyutl -sign -rawin -inkey reviewer-private.pem -in contract.json -out reviewer.sig. An operator must enroll ONLY its trusted public key in /etc/kitt/cleanroom-reviewer.pub.pem as root-owned, non-writable by other users. Copy the detached 64-byte reviewer.sig to the private seal RUN_ID directory; do not copy the private key. No signature, changed contract or absent root-owned public key means BLOCKED_ENV.
6. Prepare a separately reviewed local OCI image containing an offline implementer runtime and an offline model. Its fixed entrypoint must be /usr/local/bin/kitt-cleanroom-implementer. The implementer must honor --contract and --contract-sha256. Only an immutable sha256:<ID> image is accepted.
7. Prepare a Wine/ReactOS implementation checkout with no analyst-owned files, tool history, proprietary code or model memory. It must be separate from the private seal root. Explicitly authorize execution:
   kitt re cleanroom run-offline RUN_ID --sha256 SHA256 --wine-source /ABS/WINE_CHECKOUT --image sha256:IMAGE_ID --approve
   The runner additionally verifies the detached review signature and forces local Docker/Podman context. It refuses network access, extra mounts, host Docker socket, elevated container capabilities and mutable image tags. It uses a read-only container root, limited CPU/RAM/processes, and passes only the canonical spec file, not the receipt.
8. Review B's generated code for independence; build winetest, run known behavioral cases, compare guest Oracle receipts and preserve evidence. Neither an OCI exit code nor a contract hash alone establishes Clean-Room success.

Limitations and mandatory gates:
- The seal's reviewer identifier is only an assertion. Launch requires a detached Ed25519 signature verified with the root-owned public trust anchor. The reviewer must keep the private key outside both agents and outside the KITT host. This still does not prove a trusted timestamp or independent source provenance.
- An OCI container shares the Linux host kernel; use a dedicated VM for adversarial/untrusted binaries. The current pilot supports only the fixed benign SHA-pinned Windows probe.
- The offline image and local model are not supplied by this change; lack of either must block instead of granting egress.
- The launcher does not provide a verified independent winetest build or Windows Oracle E2E; SUCCESS must not be inferred from IMPLEMENTER_EXITED_UNVERIFIED.
- Path containment and scanner heuristics are not proof against all semantic leakage. Keep A and B as independently isolated OS-level principals, prohibit shared cloud sessions, memories, artifacts and direct children.send, and review new workflow integrations before enabling them.
- The image ID is operator-selected and must be trusted via independent build provenance. The runner does not build or attest the image.


## Complete offline execution path (Agent CLI 0.88.10)

The image builder uses only an operator-preloaded base image, a locally provided
GGUF file and the local `llama-cli` executable. The base image must already
contain Python 3, Git, a POSIX shell and the desired Wine build/test toolchain.
No model is fetched and no remote model session is used.

`kitt re cleanroom build-image --base-image sha256:BASE --model /ABS/model.gguf --llama-cli /ABS/llama-cli --engine docker`

The generated `sha256:IMAGE` is a build artifact, **not** attestation of
the supplied model, executable or base-image provenance. Use only independently
reviewed and license-compliant assets. The bundled offline agent reads only the
approved JSON behavior contract and bounded references from a *clean Wine
implementation checkout*. It generates one scoped Git patch, with no shell tools,
network or shared conversation. The host subsequently checks all changed paths.

`kitt re cleanroom run RUN_ID --sha256 CONTRACT_SHA --wine-source /ABS/WINE --image sha256:IMPLEMENTER --validator-image sha256:VALIDATOR --test-script /ABS/approved-test.sh --test-signature /ABS/approved-test.sig --test-sha256 TEST_SHA --approve`

The test plan is a POSIX shell file that an independent reviewer must sign
with Ed25519 using the public key trusted by the host. Unlike the model, the
independent validation stage runs a fixed operator-approved test script in a
*separate, pinned, networkless OCI image*. It receives only the approved checkout
and signed test plan, never the analyst workspace or private handoff artifacts.
The plan must perform the appropriate build and winetest for that checkout;
a successful shell exit alone is NOT proof that any particular test ran.

Use `--require-fixed-oracle-pilot` to check the existing Dockur Windows
event-auto-reset scenario before implementing. That pilot confirms only
the fixed probe, not behavior of the modified API. Full independently observed
Windows-vs-new-Wine differential conformance is still outstanding.

The final state is `NEEDS_INDEPENDENT_ORACLE_CONFORMANCE` even after
test command exit 0, and the CLI returns nonzero until the external conformance
gate is implemented and verified. Never classify this state as 100% Clean-Room.
A chain of SHA-256 receipt files is written into private operator storage,
but complete chain integrity needs an independently retained signed root digest.

Limitations: image input must be preinstalled, no automated Windows license
acquisition or arbitrary proprietary binary execution is permitted, and the
independent reviewer private key must not exist on either agent host.
The OCI containers share the Linux kernel; move untrusted samples to isolated
VMs. The offline agent uses one bounded local model generation, not an open-ended
autonomous retry loop. Runtime and legal/source-provenance verification require
independent evidence.
