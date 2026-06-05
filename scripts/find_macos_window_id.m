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

    CFArrayRef windows = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID);
    if (!windows) {
        if (query) {
            CFRelease(query);
        }
        return 1;
    }

    int found_count = 0;
    int first_number = 0;
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

        if (allow_multiple) {
            printf("%d\n", number);
        } else {
            first_number = number;
        }
        found_count++;
    }

    CFRelease(windows);
    if (query) {
        CFRelease(query);
    }

    if (found_count == 0) {
        fprintf(stderr, "no matching on-screen window found\n");
        return 2;
    }
    if (found_count > 1 && !allow_multiple) {
        fprintf(stderr, "multiple matching windows found\n");
        return 3;
    }
    if (!allow_multiple) {
        printf("%d\n", first_number);
    }
    return 0;
}
