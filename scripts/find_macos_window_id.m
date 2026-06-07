#include <ApplicationServices/ApplicationServices.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <string.h>

static int contains(CFStringRef haystack, CFStringRef needle) {
    if (!haystack || !needle) {
        return 0;
    }
    CFRange range = CFStringFind(haystack, needle, kCFCompareCaseInsensitive);
    return range.location != kCFNotFound;
}

static int layer_is_normal(CFDictionaryRef window) {
    int layer = 1;
    CFNumberRef layer_ref = CFDictionaryGetValue(window, kCGWindowLayer);
    if (layer_ref) {
        CFNumberGetValue(layer_ref, kCFNumberIntType, &layer);
    }
    return layer == 0;
}

/* A window is capturable only if it is currently drawn on a display. Minimized
 * windows and windows on other Spaces report kCGWindowIsOnscreen == false and
 * have no bitmap to capture. */
static int window_is_onscreen(CFDictionaryRef window) {
    CFBooleanRef onscreen_ref = CFDictionaryGetValue(window, kCGWindowIsOnscreen);
    return onscreen_ref && CFBooleanGetValue(onscreen_ref);
}

static int window_number(CFDictionaryRef window, int *number) {
    CFNumberRef number_ref = CFDictionaryGetValue(window, kCGWindowNumber);
    return number_ref && CFNumberGetValue(number_ref, kCFNumberIntType, number);
}

int main(int argc, char **argv) {
    int allow_multiple = 0;
    int frontmost = 0;
    const char *query_arg = NULL;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--allow-multiple") == 0) {
            allow_multiple = 1;
        } else if (strcmp(argv[i], "--frontmost") == 0) {
            frontmost = 1;
        } else {
            query_arg = argv[i];
        }
    }

    if (!frontmost && !query_arg) {
        fprintf(stderr, "usage: find_macos_window_id [--allow-multiple] [--frontmost] <window-or-app-name>\n");
        return 64;
    }

    CFStringRef query = NULL;
    if (query_arg) {
        query = CFStringCreateWithCString(NULL, query_arg, kCFStringEncodingUTF8);
        if (!query) {
            return 64;
        }
    }

    /* Enumerate ALL windows (not just on-screen ones) so that a minimized or
     * off-Space window that matches the query is still discovered. We classify
     * each match with window_is_onscreen() below to decide whether it can be
     * captured or whether to report it as present-but-not-capturable. */
    CFArrayRef windows = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID);
    if (!windows) {
        if (query) {
            CFRelease(query);
        }
        return 1;
    }

    int capturable_count = 0;   /* matched, normal-layer, currently on-screen */
    int present_count = 0;      /* matched and normal-layer, any on-screen state */
    int first_capturable = 0;
    CFIndex count = CFArrayGetCount(windows);
    for (CFIndex i = 0; i < count; i++) {
        CFDictionaryRef window = CFArrayGetValueAtIndex(windows, i);
        if (!layer_is_normal(window)) {
            continue;
        }

        int number = 0;
        if (!window_number(window, &number)) {
            continue;
        }

        if (frontmost) {
            /* The frontmost capturable window is the first on-screen, normal-layer
             * window in front-to-back order; never return a minimized one. */
            if (!window_is_onscreen(window)) {
                continue;
            }
            printf("%d\n", number);
            CFRelease(windows);
            if (query) {
                CFRelease(query);
            }
            return 0;
        }

        CFStringRef owner = CFDictionaryGetValue(window, kCGWindowOwnerName);
        CFStringRef title = CFDictionaryGetValue(window, kCGWindowName);
        if (!contains(owner, query) && !contains(title, query)) {
            continue;
        }

        present_count++;
        if (!window_is_onscreen(window)) {
            continue;   /* minimized or off-Space: present but not capturable */
        }
        if (allow_multiple) {
            printf("%d\n", number);
        } else {
            first_capturable = number;
        }
        capturable_count++;
    }

    CFRelease(windows);
    if (query) {
        CFRelease(query);
    }

    if (frontmost) {
        /* Reached only when no on-screen normal-layer window exists. */
        fprintf(stderr, "no matching on-screen window found\n");
        return 2;
    }

    if (capturable_count == 0) {
        if (present_count > 0) {
            /* A window matched but is minimized or on another Space, so it has no
             * bitmap to capture. CoreGraphics cannot reliably tell minimized from
             * off-Space, so emit the conservative "unknown" reason token. */
            fprintf(stderr, "unknown\n");
            return 4;
        }
        fprintf(stderr, "no matching on-screen window found\n");
        return 2;
    }
    if (capturable_count > 1 && !allow_multiple) {
        fprintf(stderr, "multiple matching windows found\n");
        return 3;
    }
    if (!allow_multiple) {
        printf("%d\n", first_capturable);
    }
    return 0;
}
