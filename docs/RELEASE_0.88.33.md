# Agent CLI 0.88.33

Goal retries now carry bounded prior host-read evidence from the same contract item through the durable RuntimeStateStore. Read/list/search results (including successful members of partially failed batches) are reusable in the next model prompt, reducing unnecessary reinspection. Entries are limited to 12 records and 800 characters per result and invalidated after relevant host mutations. Semantic memory remains optional; previously captured evidence is labeled as untrusted source data, never authority.

The fix requires installing the updated Agent CLI; it does not itself repair unavailable Bubblewrap >=0.12 on the workstation, nor does it execute Wine clean-room Oracle checks. CI validates repository contracts but cannot certify the user's authenticated WebChat and guest VM environments.
