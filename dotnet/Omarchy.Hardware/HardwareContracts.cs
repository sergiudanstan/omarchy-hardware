namespace Omarchy.Hardware;

public enum DeviceFamily
{
    RaspberryPi,
    Jetson,
    Microcontroller,
    SiemensLogo,
    SiemensS7,
    OmronPlc,
    SchneiderPlc,
    WeintekHmi,
}

public enum OperationSafety
{
    ReadOnly,
    StateChanging,
    Destructive,
}

public sealed record HardwareOperation(
    string Id,
    OperationSafety Safety,
    bool RequiresConfirmation);

public sealed record CapabilityOperation(
    string Id,
    OperationSafety Safety,
    bool Available);

public interface IHardwareAdapter
{
    DeviceFamily Family { get; }
    IReadOnlyList<HardwareOperation> Operations { get; }
    Task<HardwareInventory> InspectAsync(
        string identity,
        CancellationToken cancellationToken = default);
}

public sealed class AdapterRegistry(IEnumerable<IHardwareAdapter> adapters)
{
    private readonly IReadOnlyDictionary<DeviceFamily, IHardwareAdapter> _adapters =
        adapters.ToDictionary(adapter => adapter.Family);

    public IHardwareAdapter Get(DeviceFamily family) =>
        _adapters.TryGetValue(family, out var adapter)
            ? adapter
            : throw new InvalidOperationException($"No adapter is registered for {family}.");
}
