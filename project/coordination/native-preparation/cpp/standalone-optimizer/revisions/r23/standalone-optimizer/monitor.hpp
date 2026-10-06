#pragma once
#include <filesystem>
int progress_monitor(const std::filesystem::path& status);
void start_progress_monitor(const std::filesystem::path& status);
