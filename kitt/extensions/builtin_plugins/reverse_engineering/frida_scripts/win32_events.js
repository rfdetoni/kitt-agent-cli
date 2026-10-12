// Fixed observation-only tracing for a disposable Windows guest test executable.
// No arbitrary memory reads, writes, argument capture or network calls.
'use strict';

const names = ['CreateEventW', 'SetEvent', 'WaitForSingleObject'];
for (const name of names) {
  try {
    const pointer = Module.getGlobalExportByName(name);
    Interceptor.attach(pointer, {
      onEnter(_args) {
        send({ kind: 'api_enter', api: name });
      }
    });
  } catch (_error) {
    // An optional export may be absent on this Windows/Wine version.
  }
}
