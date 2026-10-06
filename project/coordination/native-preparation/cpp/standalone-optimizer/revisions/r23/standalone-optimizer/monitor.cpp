#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <nlohmann/json.hpp>
#include "monitor.hpp"
#include <fstream>
#include <sstream>
namespace {
std::filesystem::path statusPath;
HWND label;
LRESULT CALLBACK proc(HWND w,UINT m,WPARAM wp,LPARAM lp){
 if(m==WM_CREATE){label=CreateWindowW(L"STATIC",L"C++ optimizer starting. Totals and ETA unknown.",WS_CHILD|WS_VISIBLE|SS_LEFT,20,20,650,380,w,nullptr,nullptr,nullptr);SendMessageW(label,WM_SETFONT,reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)),TRUE);SetTimer(w,1,1000,nullptr);return 0;}
 if(m==WM_TIMER){try{std::ifstream f(statusPath);if(f){auto j=nlohmann::json::parse(f);std::ostringstream s;s<<"C++ optimizer: "<<j.value("state",std::string("unknown"))<<" / "<<j.value("stage",std::string("unknown"))<<"\r\n\r\n";for(auto key:{"savedRecords","invalidCandidates","executionErrors","successfulOutcomes","scoredOutcomes","freshSavedRecords","restoredRecords","duplicateSkips","completedTrials","elapsedMs","totalElapsedMs","startupMs","error"})if(j.contains(key))s<<key<<": "<<j[key].dump()<<"\r\n";const double elapsed=j.value("totalElapsedMs",j.value("elapsedMs",0.0));if(elapsed>0)s<<"Durable saves/s (this run): "<<j.value("freshSavedRecords",0.0)*1000.0/elapsed<<"\r\n";s<<"\r\nOverall percentage / ETA: unknown\r\nOutput: "<<statusPath.parent_path().string()<<"\r\nClosing this monitor leaves the job running.";std::string t=s.str();std::wstring wide(t.begin(),t.end());SetWindowTextW(label,wide.c_str());}}catch(...){/* transient atomic-write replacement; keep last observation */}return 0;}
 if(m==WM_DESTROY){PostQuitMessage(0);return 0;}return DefWindowProcW(w,m,wp,lp);
}
}
int progress_monitor(const std::filesystem::path& path){statusPath=path;auto instance=GetModuleHandleW(nullptr);WNDCLASSW wc{};wc.lpfnWndProc=proc;wc.hInstance=instance;wc.lpszClassName=L"KaCppOptimizerProgress";wc.hbrBackground=reinterpret_cast<HBRUSH>(COLOR_WINDOW+1);wc.hCursor=LoadCursor(nullptr,IDC_ARROW);if(!RegisterClassW(&wc))return 2;auto window=CreateWindowW(wc.lpszClassName,L"C++ optimizer progress",WS_OVERLAPPED|WS_CAPTION|WS_SYSMENU|WS_MINIMIZEBOX,CW_USEDEFAULT,CW_USEDEFAULT,710,475,nullptr,nullptr,instance,nullptr);if(!window)return 2;ShowWindow(window,SW_SHOW);MSG msg{};while(GetMessageW(&msg,nullptr,0,0)>0){TranslateMessage(&msg);DispatchMessageW(&msg);}return 0;}
void start_progress_monitor(const std::filesystem::path& status){wchar_t exe[32768]{};if(!GetModuleFileNameW(nullptr,exe,32768))throw std::runtime_error("cannot locate monitor executable");std::wstring command=L"\""+std::wstring(exe)+L"\" --monitor \""+std::filesystem::absolute(status).wstring()+L"\"";STARTUPINFOW si{};si.cb=sizeof si;PROCESS_INFORMATION pi{};if(!CreateProcessW(exe,command.data(),nullptr,nullptr,FALSE,CREATE_NO_WINDOW,nullptr,nullptr,&si,&pi))throw std::runtime_error("cannot launch progress monitor");CloseHandle(pi.hThread);CloseHandle(pi.hProcess);}
