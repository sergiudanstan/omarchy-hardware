namespace Omarchy.Hardware;

public enum WeintekTarget
{
    Cmt,
    Mt,
}

public sealed record HmiIdentity(
    WeintekTarget Target,
    string Endpoint,
    string? Model,
    string? Firmware);

public sealed record OpcUaReadResult(
    string NodeId,
    string DataType,
    object Value);

/// <summary>
/// How a session to an OPC UA endpoint is secured.
/// </summary>
/// <remarks>
/// Carried as a required argument rather than left to the implementation. An
/// interface that takes only an endpoint string cannot express an authenticated,
/// encrypted session, so every implementation of it ends up anonymous and in the
/// clear -- which is the wrong default for anything that writes to an HMI.
/// Mirrors OpcUaSecurity in mcp/omarchy_hardware/config.py.
/// </remarks>
public sealed record OpcUaSecurity(
    string Policy,
    string Mode,
    string? CertificatePath,
    string? PrivateKeyPath,
    string? TrustListPath,
    string? Username)
{
    public static OpcUaSecurity SignAndEncrypt(string certificatePath, string privateKeyPath) =>
        new("Basic256Sha256", "SignAndEncrypt", certificatePath, privateKeyPath, null, null);

    public bool IsInsecure => Mode == "None" || Policy == "None";
}

/// <summary>Transport security for an MQTT connection. Mirrors MqttSecurity in config.py.</summary>
public sealed record MqttSecurity(
    bool UseTls,
    string? CaFilePath,
    string? ClientCertificatePath,
    string? ClientKeyPath,
    string? Username)
{
    public static MqttSecurity Tls(string? caFilePath = null) =>
        new(true, caFilePath, null, null, null);

    public bool IsInsecure => !UseTls;
}

public interface IOpcUaClient
{
    Task<HmiIdentity> IdentifyAsync(
        string endpoint,
        OpcUaSecurity security,
        CancellationToken cancellationToken = default);

    Task<IReadOnlyList<OpcUaReadResult>> ReadAsync(
        string endpoint,
        OpcUaSecurity security,
        IReadOnlyList<string> allowlistedNodes,
        CancellationToken cancellationToken = default);
}

public interface IMqttClient
{
    Task PublishAsync(
        string host,
        int port,
        MqttSecurity security,
        string topic,
        string payload,
        CancellationToken cancellationToken = default);
}

public sealed class WeintekHmiAdapter(
    IOpcUaClient opcua,
    IReadOnlySet<string> allowedEndpoints,
    OpcUaSecurity security,
    IMqttClient? mqtt = null) : IHardwareAdapter
{
    public DeviceFamily Family => DeviceFamily.WeintekHmi;

    public IReadOnlyList<HardwareOperation> Operations { get; } =
    [
        new("hmi.identify", OperationSafety.ReadOnly, false),
        new("opcua.read", OperationSafety.ReadOnly, false),
        new("opcua.write", OperationSafety.Destructive, true),
        new("mqtt.subscribe", OperationSafety.StateChanging, false),
        new("mqtt.publish", OperationSafety.Destructive, true),
    ];

    public async Task<HardwareInventory> InspectAsync(
        string identity,
        CancellationToken cancellationToken = default)
    {
        identity = PolicyGuard.RequireAllowlisted(identity, allowedEndpoints, "Weintek endpoint");
        PolicyGuard.RequireSecureTransport(security.IsInsecure, "Weintek OPC UA endpoint");
        var hmi = await opcua.IdentifyAsync(identity, security, cancellationToken);
        var model = hmi.Model ?? hmi.Target.ToString();
        var firmware = hmi.Firmware is null ? null : $"firmware:{hmi.Firmware}";
        _ = mqtt;
        return new HardwareInventory(
            new CapabilityDevice(
                Family.ToString(),
                model,
                hmi.Endpoint,
                [],
                "reachable",
                RemoteText.Describe(Operations, new HashSet<string>())),
            "weintek",
            model,
            firmware,
            null,
            null,
            null,
            null);
    }
}
