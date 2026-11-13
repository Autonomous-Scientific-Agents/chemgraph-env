# Machine locks

Locks must be prepared on the target ALCF machine. No untested lock is shipped.
`scripts/prepare-lock.py` writes `lock.json` and `overlay.txt` into a **new**
directory. Commit these two files after review. The metadata records the full
base inventory, exact interpreter, loaded modules, protected packages, source
requirements, and the overlay checksum. Installation rejects base-stack drift.

Keep one lock per module/Python combination. The uv and native installers share
the lock when their base stack is identical. Lock preparation does not certify
GPU execution or IRI submission; those are separate deployment acceptance tests.
