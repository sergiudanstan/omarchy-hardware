using System.Globalization;

namespace Omarchy.Hardware;

public sealed record RemoteReadResult(bool Succeeded, string Output);

public interface IFixedRemoteReader
{
    Task<RemoteReadResult> ReadAsync(
        string identity,
        string path,
        CancellationToken cancellationToken = default);
}

internal static class RemoteText
{
    internal static string? Clean(string? value) =>
        value?.Trim().Trim('\0') is { Length: > 0 } cleaned ? cleaned : null;

    internal static double? ParseTemperature(string? value) =>
        double.TryParse(value?.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var millidegrees)
            && millidegrees is >= -100_000 and <= 200_000
            ? millidegrees / 1000
            : null;

    internal static double? ParseLoad(string? value) =>
        double.TryParse(
            value?.Split(' ', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault(),
            CultureInfo.InvariantCulture,
            out var load) && load >= 0
            ? load
            : null;

    internal static Dictionary<string, string> ParseKeyValues(string? text)
    {
        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var line in text?.Split('\n', StringSplitOptions.RemoveEmptyEntries) ?? [])
        {
            var separator = line.IndexOf('=');
            if (separator <= 0)
                continue;
            var key = line[..separator];
            var value = line[(separator + 1)..].Trim().Trim('"');
            if (key.All(c => char.IsLetterOrDigit(c) || c == '_'))
                values[key] = value;
        }
        return values;
    }

    internal static IReadOnlyList<CapabilityOperation> Describe(
        IReadOnlyList<HardwareOperation> operations,
        IReadOnlySet<string> available)
    {
        return operations
            .Select(op => new CapabilityOperation(op.Id, op.Safety, available.Contains(op.Id)))
            .ToArray();
    }
}
