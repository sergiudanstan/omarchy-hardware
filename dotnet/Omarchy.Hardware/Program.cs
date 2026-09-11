using System.Text.Json;

namespace Omarchy.Hardware;

public sealed record CapabilityDevice(
    string Family,
    string? Model,
    string Identity,
    IReadOnlyList<string> Capabilities,
    string Health);

public sealed record HardwareInventory(
    CapabilityDevice Device,
    string? OsId,
    string? OsName,
    string? OsVersion,
    string? Kernel,
    string? GpioBackend,
    double? TemperatureC,
    double? LoadAverage1M);

public static class InventoryJson
{
    public static string Serialize(HardwareInventory inventory) =>
        JsonSerializer.Serialize(inventory, new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            WriteIndented = false
        });
}

public static class Program
{
    public static void Main(string[] args)
    {
        Console.Error.WriteLine(
            "Omarchy.Hardware native orchestration foundation: adapters are not enabled yet.");
    }
}
