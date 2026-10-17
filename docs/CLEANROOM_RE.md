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
