#include "hardware_core.h"

#include <assert.h>
#include <string.h>

int main(void) {
    double value = 0.0;
    char distro[64];

    assert(oh_parse_load_1m("0.42 0.20 0.10 1/80 99", &value));
    assert(value > 0.419 && value < 0.421);
    assert(oh_parse_temperature_mc("42123\n", &value));
    assert(value > 42.122 && value < 42.124);
    assert(oh_lookup_env_value("ID=raspios\nPRETTY_NAME=\"Raspberry Pi OS\"\n",
                               "PRETTY_NAME", distro, sizeof(distro)));
    assert(strcmp(distro, "Raspberry Pi OS") == 0);
    assert(!oh_parse_temperature_mc("999999", &value));
    assert(!oh_lookup_env_value("ID=raspios\n", "MISSING", distro, sizeof(distro)));
    return 0;
}
