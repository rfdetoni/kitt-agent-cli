/* Minimal Windows/Wine differential probe. Emits one JSON object. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>

int main(void) {
    HANDLE event = CreateEventW(NULL, FALSE, FALSE, NULL);
    if (event == NULL) {
        fprintf(stderr, "CreateEventW failed: %lu\n", (unsigned long)GetLastError());
        return 1;
    }

    DWORD first = WaitForSingleObject(event, 0);
    BOOL signal_ok = SetEvent(event);
    DWORD second = WaitForSingleObject(event, 0);
    DWORD third = WaitForSingleObject(event, 0);

    printf("{\"initial\":%lu,\"set_ok\":%s,\"after_set\":%lu,\"after_auto_reset\":%lu}\n",
           (unsigned long)first,
           signal_ok ? "true" : "false",
           (unsigned long)second,
           (unsigned long)third);
    CloseHandle(event);
    return signal_ok ? 0 : 2;
}
