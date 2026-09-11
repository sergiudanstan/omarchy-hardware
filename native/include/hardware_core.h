#ifndef OMARCHY_HARDWARE_CORE_H
#define OMARCHY_HARDWARE_CORE_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Parse the first load average from /proc/loadavg-style text. */
int oh_parse_load_1m(const char *text, double *value);

/* Parse a millidegree Celsius value from sysfs thermal-zone text. */
int oh_parse_temperature_mc(const char *text, double *celsius);

/* Copy a bounded, NUL-terminated line value from KEY=VALUE text. */
int oh_lookup_env_value(const char *text, const char *key, char *out, size_t out_size);

#ifdef __cplusplus
}
#endif

#endif
