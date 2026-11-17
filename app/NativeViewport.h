#pragma once
#include <BWAPI.h>

namespace NativeViewport {
// StarCraft 1.16.1 Bitmap {u16 width, height; u8* data}, documented by BWAPI
// BW/Offsets.h and BW/Bitmap.h. This is the rendered surface, not window size.
inline BWAPI::Position size() {
    const auto bitmap = reinterpret_cast<const volatile unsigned short*>(0x006CEFF0);
    int width = bitmap[0], height = bitmap[1];
    return width >= 640 && width <= 1920 && height >= 480 && height <= 1440
        ? BWAPI::Position(width, height) : BWAPI::Position(640, 480);
}
inline BWAPI::Position cameraOffset() {
    auto value = size();
    return BWAPI::Position(value.x/2, 200+(value.y-480)/2);
}
}
