# Custom plugin workflow

Two runnable plugin examples already live under [../plugins](../plugins):

- `hello`: minimal plugin shape;
- `security_guard`: policy/security-oriented example.

When creating a plugin:

1. define a small `plugin.toml`;
2. expose narrowly scoped tools/hooks;
3. keep model-facing schemas bounded;
4. declare ownership so unload can dispose registrations;
5. never shadow built-in KITT tools;
6. opt into read-only trust only when the tool is actually side-effect free;
7. treat workspace/tool input as untrusted data.

Start from `examples/plugins/hello` and add tests for registration, unload and any security boundary your plugin touches.

Plugin execution remains inside Agent policy. A plugin registration does not grant filesystem, process, network or control-plane authority by itself.

See [../../docs/plugins.md](../../docs/plugins.md).
