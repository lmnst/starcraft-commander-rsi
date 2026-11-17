# Local PC transport

Based on BWAPI SNP_DirectIP; upstream: https://github.com/bwapi/bwapi . Original license is in COPYING.

The current version bounds the shared receive array at 256 packets, refuses overflow before increasing the count, and clamps corrupt saved counts. Each match has an independent mutex/shared-memory namespace from COMMANDER_NETWORK_SCOPE.

The game list is returned as an independent snapshot, so Storm can hold it without holding the packet-queue lock. Network callbacks are invoked without holding that queue lock. Forced send/receive and lobby-refresh contention, snapshot lifetime during delivery, and 10,000 FIFO packets are covered by validation/snp-lock-check.cpp. Earlier queue and lock regressions are preserved in the validation records.

CommanderLocalPC.snp is built from these sources and the upstream Util/Storm sources, with caps.mpq appended. The build-only Storm stub is not installed into the game directory. The launcher records the actual transport SHA256 and permits independent CPU scheduling for the two LAN clients. Bot binaries, maps and the frozen baseline remain unchanged.
