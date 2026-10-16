/* KITT-authored safe Win32 synchronization pilot: no external side effects. */
#include <windows.h>
#include <stdio.h>
int main(void) {
    HANDLE h = CreateEventW(NULL, FALSE, FALSE, NULL);
    if (!h) { puts("{\"error\":\"create_event\"}"); return 2; }
    DWORD before = WaitForSingleObject(h, 0);
    if (!SetEvent(h)) { CloseHandle(h); return 3; }
    DWORD signaled = WaitForSingleObject(h, 0);
    DWORD consumed = WaitForSingleObject(h, 0);
    CloseHandle(h);
    printf("{\"before\":%lu,\"signaled\":%lu,\"consumed\":%lu}\n",
           (unsigned long)before, (unsigned long)signaled, (unsigned long)consumed);
    return 0;
}
