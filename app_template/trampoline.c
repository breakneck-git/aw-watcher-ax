// Mach-O trampoline executable for the aw-watcher-ax.app bundle.
//
// Reads the absolute path of the real venv launcher from
// Contents/Resources/launcher-target (alongside this binary inside the
// bundle) and fork+execs it, then waits for the child and proxies its
// exit status. Exists so macOS TCC (Accessibility) tracks the .app
// bundle's own cdhash instead of the Homebrew Python interpreter the
// venv console-script shebang would otherwise end up running.
//
// Why fork+wait rather than a plain execv: the venv launcher is a
// `#!python3.11` script. If the trampoline execs it, the kernel
// replaces the trampoline's process image with the interpreter, and
// from TCC's point of view the running code is whatever Mach-O
// Homebrew happens to ship at that moment (its cdhash churns with
// every point release). By keeping this trampoline alive as the
// parent and running python as a child, the launchd-managed process
// remains our stable Mach-O; python inherits its TCC responsibility
// through the parent chain, so the Accessibility grant on the .app
// bundle survives Python upgrades and venv rebuilds.
//
// A plain bash-script wrapper has exactly the same problem as execv:
// the kernel walks shebangs (/bin/bash -> python3.11) and the final
// Mach-O is python. The trampoline has to be a real Mach-O we
// control, and it has to stay alive.
//
// Determinism: ld64's default UUID mode hashes the binary content, so
// compiling this same source on the same toolchain produces a
// bit-identical binary. Combined with `codesign --force --deep`
// (which seals launcher-target into the bundle cdhash via
// _CodeSignature/CodeResources), the .app cdhash is stable across
// reinstalls, and the user's Accessibility grant persists.

#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
    char self_path[PATH_MAX];
    uint32_t size = sizeof(self_path);
    if (_NSGetExecutablePath(self_path, &size) != 0) {
        fprintf(stderr, "aw-watcher-ax: executable path too long\n");
        return 127;
    }

    // self_path: .../Contents/MacOS/aw-watcher-ax
    // dir:       .../Contents/MacOS
    // target:    .../Contents/Resources/launcher-target
    char *last_slash = strrchr(self_path, '/');
    if (!last_slash) {
        fprintf(stderr, "aw-watcher-ax: cannot find dirname of %s\n", self_path);
        return 127;
    }
    *last_slash = '\0';

    char target_path[PATH_MAX];
    int n = snprintf(target_path, sizeof(target_path),
                     "%s/../Resources/launcher-target", self_path);
    if (n < 0 || (size_t)n >= sizeof(target_path)) {
        fprintf(stderr, "aw-watcher-ax: target path too long\n");
        return 127;
    }

    FILE *f = fopen(target_path, "r");
    if (!f) {
        fprintf(stderr, "aw-watcher-ax: cannot open %s: ", target_path);
        perror(NULL);
        return 127;
    }
    char target[PATH_MAX];
    if (!fgets(target, sizeof(target), f)) {
        fclose(f);
        fprintf(stderr, "aw-watcher-ax: launcher-target is empty\n");
        return 127;
    }
    fclose(f);
    target[strcspn(target, "\r\n")] = '\0';
    if (target[0] == '\0') {
        fprintf(stderr, "aw-watcher-ax: launcher-target is empty\n");
        return 127;
    }

    pid_t child = fork();
    if (child == -1) {
        perror("aw-watcher-ax: fork");
        return 127;
    }
    if (child == 0) {
        argv[0] = target;
        execv(target, argv);
        perror("aw-watcher-ax: execv");
        _exit(127);
    }

    int status;
    while (waitpid(child, &status, 0) == -1) {
        if (errno != EINTR) {
            perror("aw-watcher-ax: waitpid");
            return 127;
        }
    }
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)) {
        return 128 + WTERMSIG(status);
    }
    return 127;
}
