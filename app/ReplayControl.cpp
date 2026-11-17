// Native StarCraft replay controls. This module issues no unit commands.
#include <windows.h>
#include <dwmapi.h>
#include <BWAPI.h>
#include "NativeViewport.h"
#include "MatchIdentity.h"
#include <algorithm>
#include <cstdlib>
#include <fstream>
#include <cstdint>
#include <atomic>
#include <string>
#include <vector>

class ReplayControl final : public BWAPI::AIModule {
    static inline ReplayControl* active = nullptr;
    static inline std::atomic<bool> neutralCursor{false};
    static inline std::atomic<unsigned long long> blockedPolls{0};
    static inline std::atomic<HWND> inputWindow{nullptr};
    static inline std::atomic<int> diagnosticFrame{0};
    static inline PVOID faultHandler = nullptr;

    static LONG CALLBACK recordFault(EXCEPTION_POINTERS* fault) {
        DWORD code = fault->ExceptionRecord->ExceptionCode;
        if (code != EXCEPTION_ACCESS_VIOLATION && code != EXCEPTION_STACK_OVERFLOW && code != EXCEPTION_ILLEGAL_INSTRUCTION)
            return EXCEPTION_CONTINUE_SEARCH;
        char path[MAX_PATH] = {}, text[768] = {};
        HMODULE module = nullptr;
        auto address = fault->ExceptionRecord->ExceptionAddress;
        GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                          reinterpret_cast<LPCSTR>(address), &module);
        if (module) GetModuleFileNameA(module, path, MAX_PATH);
        const char* name = path;
        for (const char* p = path; *p; ++p) if (*p == '\\' || *p == '/') name = p+1;
        int length = _snprintf_s(text, sizeof(text), _TRUNCATE,
            "{\"exception\":\"0x%08lx\",\"address\":\"%p\",\"module\":\"%s\",\"offset\":\"0x%lx\",\"frame\":%d}\n",
            code, address, name, static_cast<unsigned long>(reinterpret_cast<uintptr_t>(address)-reinterpret_cast<uintptr_t>(module)), diagnosticFrame.load());
        HANDLE file = CreateFileA("bwapi-data/write/replay-native-fault.jsonl", FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                  nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file != INVALID_HANDLE_VALUE) {
            DWORD written = 0;
            if (length > 0) WriteFile(file, text, length, &written, nullptr);
            CloseHandle(file);
        }
        return EXCEPTION_CONTINUE_SEARCH;
    }
    UINT_PTR timerId = 0;
    unsigned long long sequence = 0;
    ULONGLONG lastRead = 0, lastWrite = 0;
    int target = 0, speed = 42;
    bool paused = false, seeking = false, ended = false, reload = false, updating = false;
    HWND window = nullptr;
    BWAPI::Position heldCamera = BWAPI::Positions::None;
    bool cameraGuarded = false;
    ULONGLONG lastPointerRead = 0;
    int externalPointerInside = -1;
    bool physicalPointerInside = false;
    struct CameraSample { int frame, x, y; };
    std::vector<CameraSample> originalCamera;
    size_t cameraIndex = 0;
    bool ownView = false;
    bool followRecordedCamera = true;
    int perspectivePlayer = -1;
    const char* perspectiveError = "";

    void applyPerspective() {
        auto* game = BWAPI::BroodwarPtr;
        if (!ownView) return;
        game->setRevealAll(false);
        for (auto player : game->getPlayers())
            game->setVision(player, player->getID() == perspectivePlayer);
        if (!followRecordedCamera || originalCamera.empty()) return;
        int frame = game->getFrameCount();
        while (cameraIndex+1 < originalCamera.size() && originalCamera[cameraIndex+1].frame <= frame) ++cameraIndex;
        const auto& sample = originalCamera[cameraIndex];
        heldCamera = BWAPI::Position(sample.x, sample.y);
        game->setScreenPosition(heldCamera);
    }

    void loadPerspective() {
        const char* mode = std::getenv("REPLAY_VIEW_MODE");
        ownView = mode && std::string(mode) == "self";
        auto* game = BWAPI::BroodwarPtr;
        if (!ownView) {
            game->setRevealAll(true);
            for (auto player : game->getPlayers()) game->setVision(player, true);
            return;
        }
        perspectivePlayer = setting("REPLAY_SELF_PLAYER", -1);
        const char* cameraMode = std::getenv("REPLAY_CAMERA_MODE");
        followRecordedCamera = !(cameraMode && std::string(cameraMode)=="free");
        bool found = false;
        for (auto p : game->getPlayers()) if (p->getID() == perspectivePlayer && !p->isNeutral() && !p->isObserver()) found = true;
        std::ifstream input("bwapi-data/read/original-camera.txt");
        CameraSample sample{};
        while (input >> sample.frame >> sample.x >> sample.y) originalCamera.push_back(sample);
        if (!found) perspectiveError = "original_player_not_found";
        else if (followRecordedCamera && (originalCamera.empty() || originalCamera.front().frame != 0)) perspectiveError = "original_camera_missing";
        else {
            if (!followRecordedCamera) {
                auto start=game->getPlayer(perspectivePlayer)->getStartLocation();
                if(start.isValid()) {
                    auto offset=NativeViewport::cameraOffset();
                    heldCamera=BWAPI::Position(std::max(0,start.x*32+64-offset.x),std::max(0,start.y*32+40-offset.y));
                    game->setScreenPosition(heldCamera);
                }
            }
            applyPerspective();
        }
    }
    using CursorFunction = BOOL (WINAPI*)(LPPOINT);
    static inline std::atomic<CursorFunction> cursorDelegate{nullptr};
    CursorFunction originalCursor = nullptr;
    void** cursorImport = nullptr;
    HHOOK mouseHook = nullptr;

    static BOOL WINAPI gameCursor(LPPOINT point) {
        // This import may be called by display/input threads while BWAPI is
        // ending a replay. Never dereference the short-lived AIModule here.
        auto original = cursorDelegate.load();
        if (!original || !original(point)) return FALSE;
        if (neutralCursor.load() && point) {
            // Feed the game a neutral point instead of cnc-ddraw's clamped
            // right-edge position. The physical Windows cursor is untouched.
            auto viewport = NativeViewport::size();
            point->x = viewport.x/2;
            point->y = viewport.y/2;
            ++blockedPolls;
            return TRUE;
        }
        if (point) {
            // Disable all four mouse-edge scrolling zones in native render
            // coordinates. Only coordinates seen by the replay client change;
            // the real cursor remains free to reach the sidebar and controls.
            // Preserve deliberate clicks, including minimap positioning.
            const bool clicking = (GetAsyncKeyState(VK_LBUTTON) & 0x8000) ||
                                  (GetAsyncKeyState(VK_RBUTTON) & 0x8000);
            if (!clicking) {
                auto viewport = NativeViewport::size();
                LONG x = std::clamp<LONG>(point->x, 16, viewport.x-17);
                LONG y = std::clamp<LONG>(point->y, 16, viewport.y-17);
                if (x != point->x || y != point->y) ++blockedPolls;
                point->x = x;
                point->y = y;
            }
        }
        return TRUE;
    }

    static LRESULT CALLBACK filterMouse(int code, WPARAM removal, LPARAM data) {
        if (code == HC_ACTION && removal == PM_REMOVE && neutralCursor.load()) {
            MSG& message = *reinterpret_cast<MSG*>(data);
            if (message.hwnd == inputWindow.load() && message.message == WM_MOUSEMOVE)
                message.lParam = MAKELPARAM(NativeViewport::size().x/2, NativeViewport::size().y/2);
        }
        return CallNextHookEx(nullptr, code, removal, data);
    }

    void installCursorFilter() {
        auto base = reinterpret_cast<unsigned char*>(GetModuleHandleW(nullptr));
        auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
        auto nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base+dos->e_lfanew);
        auto rva = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
        if (!rva) return;
        auto imports = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(base+rva);
        for (; imports->Name; ++imports) {
            if (lstrcmpiA(reinterpret_cast<const char*>(base+imports->Name), "USER32.dll") || !imports->OriginalFirstThunk) continue;
            auto names = reinterpret_cast<IMAGE_THUNK_DATA*>(base+imports->OriginalFirstThunk);
            auto slots = reinterpret_cast<IMAGE_THUNK_DATA*>(base+imports->FirstThunk);
            for (; names->u1.AddressOfData; ++names, ++slots) {
                if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) continue;
                auto name = reinterpret_cast<IMAGE_IMPORT_BY_NAME*>(base+names->u1.AddressOfData);
                if (lstrcmpA(reinterpret_cast<const char*>(name->Name), "GetCursorPos")) continue;
                cursorImport = reinterpret_cast<void**>(&slots->u1.Function);
                if (*cursorImport == reinterpret_cast<void*>(&gameCursor)) {
                    originalCursor = cursorDelegate.load();
                    break;
                }
                DWORD prior = 0;
                if (VirtualProtect(cursorImport, sizeof(void*), PAGE_READWRITE, &prior)) {
                    originalCursor = reinterpret_cast<CursorFunction>(*cursorImport);
                    cursorDelegate.store(originalCursor);
                    InterlockedExchangePointer(reinterpret_cast<PVOID volatile*>(cursorImport), reinterpret_cast<void*>(&gameCursor));
                    DWORD ignored = 0;
                    VirtualProtect(cursorImport, sizeof(void*), prior, &ignored);
                }
                break;
            }
        }
        mouseHook = SetWindowsHookExW(WH_GETMESSAGE, filterMouse, nullptr, GetCurrentThreadId());
    }

    void removeCursorFilter() {
        if (mouseHook) { UnhookWindowsHookEx(mouseHook); mouseHook = nullptr; }
        if (cursorImport && originalCursor && *cursorImport == reinterpret_cast<void*>(&gameCursor)) {
            DWORD prior = 0;
            if (VirtualProtect(cursorImport, sizeof(void*), PAGE_READWRITE, &prior)) {
                InterlockedCompareExchangePointer(reinterpret_cast<PVOID volatile*>(cursorImport), reinterpret_cast<void*>(originalCursor), reinterpret_cast<void*>(&gameCursor));
                DWORD ignored = 0;
                VirtualProtect(cursorImport, sizeof(void*), prior, &ignored);
            }
        }
    }

    void replayPause(bool value) {
        // Native Brood War 1.16.1's pause flag, documented in BWAPI BW/Offsets.h.
        // BWAPI pauseGame queues a command and can overshoot a target by 2 frames.
        // This module is loaded only for local replays, never for live matches.
        if (BWAPI::Broodwar->isReplay())
            *reinterpret_cast<volatile std::uint32_t*>(0x006509C4) = value ? 1u : 0u;
    }

    void guardCamera() {
        const auto now = GetTickCount64();
        if (now-lastPointerRead >= 20) {
            lastPointerRead = now;
            std::ifstream input("bwapi-data/read/replay-pointer.txt");
            int value = -1;
            if (input >> value && (value == 0 || value == 1)) externalPointerInside = value;
        }
        POINT cursor{};
        RECT bounds{};
        // cnc-ddraw hooks GetCursorPos/GetClientRect/ScreenToClient for its
        // virtual render surface. Those APIs can report a clamped edge point
        // after the real pointer has already entered the sidebar.
        if (!window || !GetPhysicalCursorPos(&cursor)) return;
        if (FAILED(DwmGetWindowAttribute(window, DWMWA_EXTENDED_FRAME_BOUNDS, &bounds, sizeof(bounds))) &&
            !GetWindowRect(window, &bounds)) return;
        HWND under = WindowFromPhysicalPoint(cursor);
        DWORD foregroundPid = 0;
        GetWindowThreadProcessId(GetForegroundWindow(), &foregroundPid);
        physicalPointerInside = foregroundPid == GetCurrentProcessId() && PtInRect(&bounds, cursor) &&
                                (under == window || IsChild(window, under));
        // An external "outside" signal may protect an overlay too. An old or
        // stale "inside" signal must never override a physical outside point.
        bool inside = physicalPointerInside && externalPointerInside != 0;
        neutralCursor.store(!inside);
        if (inside) {
            heldCamera = BWAPI::Broodwar->getScreenPosition();
            cameraGuarded = false;
        } else {
            // Free the physical cursor for the sidebar, but clear the game's
            // last edge-hover position and retain the last intentional view.
            if (!cameraGuarded)
                PostMessageW(window, WM_MOUSEMOVE, 0, MAKELPARAM(NativeViewport::size().x/2, NativeViewport::size().y/2));
            cameraGuarded = true;
            if (heldCamera.isValid()) BWAPI::Broodwar->setScreenPosition(heldCamera);
        }
    }

    static BOOL CALLBACK findWindow(HWND candidate, LPARAM result) {
        DWORD pid = 0;
        GetWindowThreadProcessId(candidate, &pid);
        if (pid == GetCurrentProcessId() && IsWindowVisible(candidate) && !GetWindow(candidate, GW_OWNER)) {
            *reinterpret_cast<HWND*>(result) = candidate;
            return FALSE;
        }
        return TRUE;
    }

    static void CALLBACK timer(HWND, UINT, UINT_PTR, DWORD) {
        if (active) active->update();
    }

    static int setting(const char* key, int fallback) {
        const char* value = std::getenv(key);
        return value ? std::atoi(value) : fallback;
    }

    void publish() {
        std::ofstream output("bwapi-data/write/replay-status.tmp", std::ios::trunc);
        output << "{\"frame\":" << BWAPI::Broodwar->getFrameCount()
               << ",\"total\":" << BWAPI::Broodwar->getReplayFrameCount()
               << ",\"target\":" << target
               << ",\"paused\":" << (paused ? "true" : "false")
               << ",\"seeking\":" << (seeking ? "true" : "false")
               << ",\"ended\":" << (ended ? "true" : "false")
               << ",\"reload\":" << (reload ? "true" : "false")
               << ",\"window_handle\":" << reinterpret_cast<uintptr_t>(window)
               << ",\"process_id\":" << GetCurrentProcessId()
               << ",\"camera_guarded\":" << (cameraGuarded ? "true" : "false")
               << ",\"physical_pointer_inside\":" << (physicalPointerInside ? "true" : "false")
               << ",\"controller_version\":\"stable-replay-v5\""
               << ",\"viewport\":[" << NativeViewport::size().x << ',' << NativeViewport::size().y << ']'
               << ",\"mouse_edge_scroll\":false"
               << ",\"cursor_filter_installed\":" << (cursorImport && *cursorImport == reinterpret_cast<void*>(&gameCursor) ? "true" : "false")
               << ",\"blocked_cursor_polls\":" << blockedPolls.load()
               << ",\"camera\":[" << BWAPI::Broodwar->getScreenPosition().x << ',' << BWAPI::Broodwar->getScreenPosition().y << ']'
               << ",\"actual_paused\":" << (BWAPI::Broodwar->isPaused() ? "true" : "false")
               << ",\"perspective\":\"" << (ownView ? "self" : "all") << "\""
               << ",\"perspective_player\":" << perspectivePlayer
               << ",\"perspective_error\":\"" << perspectiveError << "\""
               << ",\"recorded_camera\":" << (ownView && !originalCamera.empty() ? "true" : "false")
               << ",\"camera_follow\":" << (ownView && followRecordedCamera ? "true" : "false")
               << ",\"camera_sample_frame\":" << (originalCamera.empty() ? -1 : originalCamera[cameraIndex].frame)
               << ",\"vision_mask\":" << *reinterpret_cast<volatile std::uint32_t*>(0x006D0F18)
               << ",\"reveal_all\":" << (*reinterpret_cast<volatile std::uint32_t*>(0x006D0F1C) ? "true" : "false")
               << ",\"sequence\":" << sequence << "}\n";
        output.close();
        MoveFileExA("bwapi-data/write/replay-status.tmp",
                    "bwapi-data/write/replay-status.json", MOVEFILE_REPLACE_EXISTING);
        lastWrite = GetTickCount64();
    }

    void applyPlayback() {
        auto* game = BWAPI::BroodwarPtr;
        if (seeking) {
            game->setLocalSpeed(0);
            game->setFrameSkip(1);
            replayPause(false);
        } else {
            game->setFrameSkip(1);
            game->setLocalSpeed(speed);
            replayPause(paused);
        }
    }

public:
    ~ReplayControl() override {
        if (timerId) { KillTimer(nullptr, timerId); timerId = 0; }
        removeCursorFilter();
        if (active == this) {
            active = nullptr;
            inputWindow.store(nullptr);
            neutralCursor.store(false);
        }
    }

    void onStart() override {
        if (!BWAPI::Broodwar->isReplay()) {
            BWAPI::Broodwar->leaveGame();
            return;
        }
        PlayerColors::apply(BWAPI::BroodwarPtr,setting("REPLAY_COLOR_SELF",-1));
        MatchIdentity::save(BWAPI::BroodwarPtr);
        EnumWindows(findWindow, reinterpret_cast<LPARAM>(&window));
        inputWindow.store(window);
        // Import and timer callbacks can already be queued at onEnd. Keep
        // their code and static context alive until this game process exits.
        HMODULE pinned = nullptr;
        GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                          reinterpret_cast<LPCSTR>(&gameCursor), &pinned);
        if (!faultHandler) faultHandler = AddVectoredExceptionHandler(1, recordFault);
        heldCamera = BWAPI::Broodwar->getScreenPosition();
        loadPerspective();
        target = std::clamp(setting("REPLAY_INITIAL_FRAME", 0), 0,
                            std::max(0, BWAPI::Broodwar->getReplayFrameCount() - 1));
        paused = setting("REPLAY_INITIAL_PAUSED", 0) != 0;
        speed = std::clamp(setting("REPLAY_INITIAL_SPEED", 42), 1, 168);
        seeking = target > 0;
        applyPlayback();
        active = this;
        installCursorFilter();
        // Replay pause can suppress AIModule frame callbacks. Poll on the same
        // game thread so controls remain responsive while native playback is paused.
        timerId = SetTimer(nullptr, 0, 80, timer);
        publish();
    }

    void onFrame() override { update(); }

    void update() {
        if (updating || ended) return;
        struct TickGuard { bool& flag; TickGuard(bool& f) : flag(f) { flag = true; } ~TickGuard() { flag = false; } } guard(updating);
        auto* game = BWAPI::BroodwarPtr;
        if (!game->isReplay() || ended) return;
        diagnosticFrame.store(game->getFrameCount());
        guardCamera();
        applyPerspective();
        const auto now = GetTickCount64();
        if (now - lastRead >= 80) {
            lastRead = now;
            std::ifstream input("bwapi-data/read/replay-command.txt");
            unsigned long long incoming = 0;
            std::string action;
            int frame = 0, pauseValue = 0, requestedSpeed = 42;
            if (input >> incoming >> action >> frame >> pauseValue >> requestedSpeed &&
                incoming > sequence) {
                sequence = incoming;
                paused = pauseValue != 0;
                speed = std::clamp(requestedSpeed, 1, 168);
                if (action == "seek") {
                    target = std::clamp(frame, 0, std::max(0, game->getReplayFrameCount() - 1));
                    reload = target < game->getFrameCount();
                    seeking = !reload && target > game->getFrameCount();
                }
                applyPlayback();
                publish();
            }
        }
        if (seeking && game->getFrameCount() >= target) {
            seeking = false;
            applyPlayback();
            publish();
        }
        if (seeking && target - game->getFrameCount() < 128) game->setFrameSkip(1);
        if (!seeking && !reload && game->isPaused() != paused) applyPlayback();
        if (now - lastWrite >= 150) publish();
    }

    void onEnd(bool) override {
        if (timerId) { KillTimer(nullptr, timerId); timerId = 0; }
        removeCursorFilter();
        inputWindow.store(nullptr);
        neutralCursor.store(false);
        active = nullptr;
        ended = true;
        seeking = false;
        publish();
    }
};

extern "C" void gameInit(BWAPI::Game* game) { BWAPI::BroodwarPtr = game; }
extern "C" BWAPI::AIModule* newAIModule() { return new ReplayControl(); }
