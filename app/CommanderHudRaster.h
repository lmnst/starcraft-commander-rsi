#pragma once
#include <windows.h>
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace CommanderHud {
inline std::string utf8(const std::wstring& value) {
    int length=WideCharToMultiByte(CP_UTF8,0,value.data(),int(value.size()),nullptr,0,nullptr,nullptr);
    std::string result(length,'\0');
    if(length)WideCharToMultiByte(CP_UTF8,0,value.data(),int(value.size()),result.data(),length,nullptr,nullptr);
    return result;
}
inline std::string json(const std::wstring& value) {
    std::string result="\"";
    for(unsigned char c:utf8(value)) {
        if(c=='"' || c=='\\'){result+='\\';result+=char(c);}
        else if(c<32) {
            const char* hex="0123456789abcdef";result+="\\u00";result+=hex[c>>4];result+=hex[c&15];
        } else result+=char(c);
    }
    return result+'"';
}
struct Run {int left,right,y;unsigned char shade=3;};
struct RasterLine {std::wstring text;std::vector<Run> runs,outline;int width=0;};
// StarCraft's original bitmap font has no Chinese glyphs. Render Unicode with
// the Windows font once per changed line, then submit ordinary BWAPI screen
// strokes. There is no external overlay window or input/capture hook.
class Raster {
    HDC dc=nullptr;
    HBITMAP bitmap=nullptr;
    HGDIOBJ oldBitmap=nullptr,oldFont=nullptr;
    HFONT font=nullptr;
    std::uint32_t* pixels=nullptr;
    std::array<RasterLine,4> cache;
    bool supported=false;
    std::wstring preferredFace;
    std::wstring resolvedFace;
    DWORD quality;
public:
    // Leave StarCraft's native resource strip clear while moving above the old
    // y=26 placement. Four larger lines still end above the old five-line HUD.
    static constexpr int width=628,height=18,fontHeight=-13,top=16,pitch=18;
    explicit Raster(const wchar_t* face=L"Microsoft YaHei UI",DWORD rasterQuality=ANTIALIASED_QUALITY)
        :preferredFace(face),quality(rasterQuality){}
    Raster(const Raster&)=delete;Raster& operator=(const Raster&)=delete;
    ~Raster(){reset();}
    void reset() {
        if(dc && oldFont)SelectObject(dc,oldFont);
        if(dc && oldBitmap)SelectObject(dc,oldBitmap);
        if(font)DeleteObject(font);
        if(bitmap)DeleteObject(bitmap);
        if(dc)DeleteDC(dc);
        dc=nullptr;bitmap=nullptr;font=nullptr;oldFont=nullptr;oldBitmap=nullptr;pixels=nullptr;supported=false;
        cache={};resolvedFace.clear();
    }
    bool ready() {
        if(dc)return supported;
        dc=CreateCompatibleDC(nullptr);if(!dc)return false;
        BITMAPINFO info{};info.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);
        info.bmiHeader.biWidth=width;info.bmiHeader.biHeight=-height;
        info.bmiHeader.biPlanes=1;info.bmiHeader.biBitCount=32;info.bmiHeader.biCompression=BI_RGB;
        bitmap=CreateDIBSection(dc,&info,DIB_RGB_COLORS,reinterpret_cast<void**>(&pixels),nullptr,0);
        if(!bitmap){reset();return false;}
        oldBitmap=SelectObject(dc,bitmap);
        // Grayscale antialiasing retains stroke coverage without ClearType's
        // colored subpixels. Keep the original 13px size and line geometry.
        for(const wchar_t* face:{preferredFace.c_str(),L"SimSun",L"Microsoft YaHei"}) {
            font=CreateFontW(fontHeight,0,0,0,FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,
                OUT_DEFAULT_PRECIS,CLIP_DEFAULT_PRECIS,quality,DEFAULT_PITCH,face);
            if(!font)continue;
            auto previous=SelectObject(dc,font);if(!oldFont)oldFont=previous;
            const std::wstring probe=L"侦察避险小队全进攻分矿主基地安全集结撤退回防提示";
            std::vector<WORD> indices(probe.size());
            DWORD result=GetGlyphIndicesW(dc,probe.data(),int(probe.size()),indices.data(),GGI_MARK_NONEXISTING_GLYPHS);
            supported=result!=GDI_ERROR && std::none_of(indices.begin(),indices.end(),[](WORD glyph){return glyph==0xffff;});
            if(supported) {
                wchar_t actual[LF_FACESIZE]={};GetTextFaceW(dc,LF_FACESIZE,actual);resolvedFace=actual;
                break;
            }
            SelectObject(dc,oldFont);DeleteObject(font);font=nullptr;
        }
        SetBkMode(dc,TRANSPARENT);SetTextColor(dc,RGB(255,255,255));
        return supported;
    }
    const std::wstring& fontFace() const {return resolvedFace;}
    const RasterLine& render(size_t slot,const std::wstring& text) {
        auto& result=cache.at(slot);
        if(!ready())return result;
        if(result.text==text)return result;
        result.text=text;result.runs.clear();result.outline.clear();
        SIZE extent{};GetTextExtentPoint32W(dc,text.data(),int(text.size()),&extent);result.width=extent.cx;
        std::memset(pixels,0,width*height*sizeof(*pixels));
        TextOutW(dc,0,0,text.data(),int(text.size()));GdiFlush();
        auto shade=[&](int x,int y) {
            unsigned value=pixels[y*width+x]&255;
            return value<24?0:value<96?1:value<192?2:3;
        };
        for(int y=0;y<height;++y) {
            int x=0;
            while(x<width) {
                while(x<width && !shade(x,y))++x;
                int start=x;
                int level=x<width?shade(x,y):0;
                while(x<width && shade(x,y)==level)++x;
                if(start<x)result.runs.push_back({start,x,y,static_cast<unsigned char>(level)});
            }
        }
        // Merge a four-neighbor black outline into runs once per text change.
        // This separates the small glyphs from bright terrain without thicker
        // foreground strokes or repeated overlapping draw calls every frame.
        auto ink=[&](int x,int y) {return x>=0 && x<width && y>=0 && y<height && shade(x,y)!=0;};
        auto outline=[&](int x,int y) {return ink(x,y) || ink(x-1,y) || ink(x+1,y) || ink(x,y-1) || ink(x,y+1);};
        for(int y=-1;y<=height;++y) {
            int x=-1;
            while(x<=width) {
                while(x<=width && !outline(x,y))++x;
                int start=x;
                while(x<=width && outline(x,y))++x;
                if(start<x)result.outline.push_back({start,x,y});
            }
        }
        return result;
    }
};
}
