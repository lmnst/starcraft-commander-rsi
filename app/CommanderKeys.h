#pragma once
namespace CommanderKeys {
constexpr const char* version="toprow_commands_v17";
// Internal action IDs are stable; physical top-row keys are translated here.
constexpr char evade='1',mine='2',squad='3',all='4',outer='5',main='6',clear='7',rally='8',defend='9',reset='0';
constexpr int safeRally=0x100+'8';
constexpr int topRowMinus=0xBD; // VK_OEM_MINUS, immediately to the right of 0.
inline int heldIndex(int virtualKey) {
    return virtualKey>='0' && virtualKey<='9'?virtualKey-'0':virtualKey==topRowMinus?10:-1;
}
inline int shortcut(int key,bool ctrl,bool shift,bool alt) {
    if(heldIndex(key)<0 || ctrl || shift || alt)return 0;
    if(key==topRowMinus)return reset;
    if(key=='0')return defend;
    if(key=='9')return safeRally;
    return key;
}
inline char physical(int action) {
    return action==safeRally?'9':action==defend?'0':action==reset?'-':char(action);
}
}
