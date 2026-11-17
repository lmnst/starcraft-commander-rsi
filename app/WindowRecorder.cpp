// Normal builds retain a fail-closed executable for legacy callers. This
// branch has no graphics-capture initialization or capture-library dependency.
#if !defined(COMMANDER_ENABLE_SCREEN_CAPTURE) || !COMMANDER_ENABLE_SCREEN_CAPTURE
#include <iostream>
int wmain(int, wchar_t**) {
    std::wcerr << L"Screen capture is disabled in this build. Validate using game logs and state data.\n";
    return 64;
}
#else
// Capture only the registered game window, even when another app covers it.
#include <windows.h>
#include <d3d11.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <algorithm>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <thread>
#include <vector>

using namespace winrt;
using namespace winrt::Windows::Graphics::Capture;
using namespace winrt::Windows::Graphics::DirectX;
using namespace winrt::Windows::Graphics::DirectX::Direct3D11;
using namespace std::chrono_literals;

int wmain(int argc, wchar_t** argv) {
    if (argc != 4) return 2;
    HWND window = reinterpret_cast<HWND>(std::wcstoull(argv[1], nullptr, 10));
    const std::filesystem::path dimensions = argv[2], stop = argv[3];
    try {
        init_apartment(apartment_type::multi_threaded);
        if (!IsWindow(window) || !GraphicsCaptureSession::IsSupported()) return 3;
        com_ptr<ID3D11Device> device;
        com_ptr<ID3D11DeviceContext> context;
        check_hresult(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION,
            device.put(), nullptr, context.put()));
        com_ptr<::IInspectable> wrapped;
        check_hresult(CreateDirect3D11DeviceFromDXGIDevice(device.as<IDXGIDevice>().get(), wrapped.put()));
        auto directDevice = wrapped.as<IDirect3DDevice>();
        auto interop = get_activation_factory<GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
        GraphicsCaptureItem item{nullptr};
        check_hresult(interop->CreateForWindow(window, guid_of<GraphicsCaptureItem>(), put_abi(item)));

        auto size = item.Size();
        const int width = size.Width & ~1, height = size.Height & ~1;
        if (width < 2 || height < 2) return 4;
        auto pool = Direct3D11CaptureFramePool::CreateFreeThreaded(
            directDevice, DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, size);
        auto session = pool.CreateCaptureSession(item);
        session.IsCursorCaptureEnabled(false);
        session.StartCapture();

        D3D11_TEXTURE2D_DESC desc{};
        desc.Width = width;
        desc.Height = height;
        desc.MipLevels = 1;
        desc.ArraySize = 1;
        desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
        desc.SampleDesc.Count = 1;
        desc.Usage = D3D11_USAGE_STAGING;
        desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        com_ptr<ID3D11Texture2D> staging;
        check_hresult(device->CreateTexture2D(&desc, nullptr, staging.put()));
        std::vector<unsigned char> pixels(size_t(width) * height * 4, 0);
        std::ofstream(dimensions) << width << " " << height << '\n';
        HANDLE pipe = GetStdHandle(STD_OUTPUT_HANDLE);
        bool ready = false;
        auto next = std::chrono::steady_clock::now();
        while (IsWindow(window) && !std::filesystem::exists(stop)) {
            Direct3D11CaptureFrame frame{nullptr};
            while (auto pending = pool.TryGetNextFrame()) frame = pending;
            if (frame) {
                auto content = frame.ContentSize();
                auto access = frame.Surface().as<::Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
                com_ptr<ID3D11Texture2D> texture;
                check_hresult(access->GetInterface(IID_PPV_ARGS(texture.put())));
                int copyWidth = std::min({width, content.Width, size.Width});
                int copyHeight = std::min({height, content.Height, size.Height});
                if (copyWidth > 0 && copyHeight > 0) {
                    D3D11_BOX box{0, 0, 0, UINT(copyWidth), UINT(copyHeight), 1};
                    context->CopySubresourceRegion(staging.get(), 0, 0, 0, 0, texture.get(), 0, &box);
                    D3D11_MAPPED_SUBRESOURCE mapped{};
                    check_hresult(context->Map(staging.get(), 0, D3D11_MAP_READ, 0, &mapped));
                    if (copyWidth < width || copyHeight < height) std::fill(pixels.begin(), pixels.end(), 0);
                    for (int y = 0; y < copyHeight; ++y) {
                        std::memcpy(pixels.data() + size_t(y) * width * 4,
                            static_cast<unsigned char*>(mapped.pData) + size_t(y) * mapped.RowPitch,
                            size_t(copyWidth) * 4);
                    }
                    context->Unmap(staging.get(), 0);
                    ready = true;
                }
                frame.Close();
                if (content.Width > 0 && content.Height > 0 &&
                    (content.Width != size.Width || content.Height != size.Height)) {
                    size = content;
                    pool.Recreate(directDevice, DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, size);
                }
            }
            if (ready) {
                DWORD written = 0;
                if (!WriteFile(pipe, pixels.data(), DWORD(pixels.size()), &written, nullptr) ||
                    written != pixels.size()) break;
            }
            next += 40ms;
            const auto now = std::chrono::steady_clock::now();
            if (next < now - 40ms) next = now;
            std::this_thread::sleep_until(next);
        }
        session.Close();
        pool.Close();
        return ready ? 0 : 5;
    } catch (const hresult_error& error) {
        std::wcerr << L"Game window capture failed: " << error.message().c_str()
                   << L" (" << std::hex << error.code().value << L")\n";
        return 1;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
#endif
