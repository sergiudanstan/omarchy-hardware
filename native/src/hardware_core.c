#include "hardware_core.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>

int oh_parse_load_1m(const char *text, double *value) {
    char *end = NULL;
    double parsed;

    if (text == NULL || value == NULL) {
        return 0;
    }
    errno = 0;
    parsed = strtod(text, &end);
    if (end == text || errno == ERANGE || parsed < 0.0) {
        return 0;
    }
    *value = parsed;
    return 1;
}

int oh_parse_temperature_mc(const char *text, double *celsius) {
    char *end = NULL;
    long millidegrees;

    if (text == NULL || celsius == NULL) {
        return 0;
    }
    errno = 0;
    millidegrees = strtol(text, &end, 10);
    if (end == text || errno == ERANGE || millidegrees < -100000 || millidegrees > 200000) {
        return 0;
    }
    *celsius = (double)millidegrees / 1000.0;
    return 1;
}

int oh_lookup_env_value(const char *text, const char *key, char *out, size_t out_size) {
    size_t key_length;
    const char *line;

    if (text == NULL || key == NULL || out == NULL || out_size == 0 || key[0] == '\0') {
        return 0;
    }
    key_length = strlen(key);
    line = text;
    while (*line != '\0') {
        const char *end = strchr(line, '\n');
        size_t line_length = end == NULL ? strlen(line) : (size_t)(end - line);
        if (line_length > key_length && strncmp(line, key, key_length) == 0 && line[key_length] == '=') {
            const char *value = line + key_length + 1;
            size_t value_length = line_length - key_length - 1;
            if (value_length >= out_size) {
                return 0;
            }
            memcpy(out, value, value_length);
            out[value_length] = '\0';
            if (value_length >= 2 && out[0] == '"' && out[value_length - 1] == '"') {
                memmove(out, out + 1, value_length - 2);
                out[value_length - 2] = '\0';
            }
            return 1;
        }
        if (end == NULL) {
            break;
        }
        line = end + 1;
    }
    return 0;
}

int oh_parse_throttled(const char *text, unsigned long *flags) {
    const char *hex = text;
    char *end = NULL;
    unsigned long parsed;

    if (text == NULL || flags == NULL) {
        return 0;
    }
    hex = strstr(text, "0x");
    if (hex == NULL) {
        hex = strchr(text, '=');
        hex = hex == NULL ? text : hex + 1;
    }
    errno = 0;
    parsed = strtoul(hex, &end, 16);
    if (end == hex || errno == ERANGE) {
        return 0;
    }
    *flags = parsed;
    return 1;
}

int oh_pi_generation(const char *model, char *out, size_t out_size) {
    const char *label = "unknown";

    if (model == NULL || out == NULL || out_size == 0) {
        return 0;
    }
    if (strstr(model, "Raspberry Pi 5") != NULL) {
        label = "pi5";
    } else if (strstr(model, "Raspberry Pi 4") != NULL) {
        label = "pi4";
    } else if (strstr(model, "Raspberry Pi 3") != NULL) {
        label = "pi3";
    } else if (strstr(model, "Raspberry Pi 2") != NULL) {
        label = "pi2";
    } else if (strstr(model, "Raspberry Pi Zero 2") != NULL) {
        label = "pi_zero2";
    } else if (strstr(model, "Raspberry Pi Zero") != NULL) {
        label = "pi_zero";
    }
    if (strlen(label) + 1 > out_size) {
        return 0;
    }
    memcpy(out, label, strlen(label) + 1);
    return 1;
}
