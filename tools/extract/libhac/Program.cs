using System.Text.RegularExpressions;
using LibHac;
using LibHac.Common;
using LibHac.Common.Keys;
using LibHac.Fs;
using LibHac.Fs.Fsa;
using LibHac.FsSystem;
using LibHac.Spl;
using LibHac.Tools.Es;
using LibHac.Tools.Fs;
using LibHac.Tools.FsSystem;
using LibHac.Tools.FsSystem.NcaUtils;
using Path = System.IO.Path;

if (args.Length != 5)
{
    Console.Error.WriteLine("usage: LibHacExtractor <prod.keys> <title.keys|-> <base-nca-folder> <update-nca-folder> <output-folder>");
    return 2;
}

try
{
    ExtractDump(args[0], args[1] == "-" ? null : args[1], args[2], args[3], args[4]);
    return 0;
}
catch (Exception exception)
{
    string message = Regex.Replace(exception.GetBaseException().Message, @"(?i)\b[0-9a-f]{32,}\b", "[redacted]");
    Console.Error.WriteLine($"LibHac package import failed ({exception.GetBaseException().GetType().Name}): {message}");
    return 1;
}

static void ExtractDump(string prodKeys, string? titleKeys, string baseRoot, string updateRoot, string outputRoot)
{
    var keySet = KeySet.CreateDefaultKeySet();
    ExternalKeyReader.ReadKeyFile(keySet, prodKeys, null, titleKeys, null, null);
    ImportTickets(keySet, baseRoot);
    ImportTickets(keySet, updateRoot);

    List<OpenedNca> baseNcas = OpenNcas(keySet, baseRoot);
    List<OpenedNca> updateNcas = OpenNcas(keySet, updateRoot);
    try
    {
        const ulong baseTitleId = 0x0100000000010000;
        var titleIds = new HashSet<ulong> { baseTitleId, baseTitleId + 0x800 };

        OpenedNca baseProgram = FindExactlyOne(baseNcas, nca =>
            titleIds.Contains(nca.Header.TitleId) && nca.Header.ContentType == NcaContentType.Program &&
            nca.Nca.SectionExists(NcaSectionType.Code) && nca.Nca.SectionExists(NcaSectionType.Data), "base program NCA");
        OpenedNca updateProgram = FindExactlyOne(updateNcas, nca =>
            titleIds.Contains(nca.Header.TitleId) && nca.Header.ContentType == NcaContentType.Program &&
            nca.Nca.SectionExists(NcaSectionType.Code) && nca.Nca.SectionExists(NcaSectionType.Data), "update program NCA");
        OpenedNca updateControl = FindExactlyOne(updateNcas, nca =>
            titleIds.Contains(nca.Header.TitleId) && nca.Header.ContentType == NcaContentType.Control &&
            nca.Nca.SectionExists(NcaSectionType.Data), "update control NCA");

        Directory.CreateDirectory(outputRoot);
        Console.WriteLine("Extracting base and update ExeFS, update control data, and merged RomFS...");
        ExtractSection(baseProgram.Nca, NcaSectionType.Code, Path.Combine(outputRoot, "exefs-1.0.0"));
        ExtractSection(updateProgram.Nca, NcaSectionType.Code, Path.Combine(outputRoot, "exefs"));
        ExtractSection(updateControl.Nca, NcaSectionType.Data, Path.Combine(outputRoot, "control"));
        ExtractPatchedSection(baseProgram.Nca, updateProgram.Nca, NcaSectionType.Data,
            Path.Combine(outputRoot, "romfs"));
        Console.WriteLine("LibHac package import completed.");
    }
    finally
    {
        foreach (OpenedNca nca in baseNcas.Concat(updateNcas)) nca.Dispose();
    }
}

static void ImportTickets(KeySet keySet, string root)
{
    foreach (string path in Directory.EnumerateFiles(root, "*.tik", SearchOption.AllDirectories))
    {
        using FileStream stream = File.OpenRead(path);
        var ticket = new Ticket(stream);
        byte[]? titleKey = ticket.GetTitleKey(keySet);
        if (titleKey is not { Length: 16 }) continue;

        // Ticket keys are loaded into LibHac's in-memory key set and never written to disk.
        keySet.ExternalKeySet.Add(new RightsId(ticket.RightsId), new AccessKey(titleKey));
    }
}

static List<OpenedNca> OpenNcas(KeySet keySet, string root)
{
    var result = new List<OpenedNca>();
    foreach (string path in Directory.EnumerateFiles(root, "*.nca", SearchOption.AllDirectories))
    {
        try
        {
            result.Add(new OpenedNca(keySet, path));
        }
        catch
        {
            // Other NCA types in a game package can use unrelated per-title keys.
        }
    }

    return result;
}

static OpenedNca FindExactlyOne(IEnumerable<OpenedNca> ncas, Func<OpenedNca, bool> predicate, string description)
{
    OpenedNca[] matches = ncas.Where(predicate).ToArray();
    if (matches.Length != 1)
        throw new InvalidOperationException($"expected one {description}, found {matches.Length}");
    return matches[0];
}

static void ExtractSection(Nca nca, NcaSectionType section, string destination)
{
    IFileSystem fileSystem = nca.OpenFileSystem(section, IntegrityCheckLevel.ErrorOnInvalid);
    try
    {
        CopyFileSystem(fileSystem, destination);
    }
    finally
    {
        fileSystem.Dispose();
    }
}

static void ExtractPatchedSection(Nca baseNca, Nca updateNca, NcaSectionType section, string destination)
{
    IFileSystem fileSystem = baseNca.OpenFileSystemWithPatch(updateNca, section, IntegrityCheckLevel.ErrorOnInvalid);
    try
    {
        CopyFileSystem(fileSystem, destination);
    }
    finally
    {
        fileSystem.Dispose();
    }
}

static void CopyFileSystem(IFileSystem source, string destination)
{
    Directory.CreateDirectory(destination);
    CopyDirectory(source, "/", destination);
}

static void CopyDirectory(IFileSystem source, string sourcePath, string destinationPath)
{
    foreach (DirectoryEntryEx entry in source.EnumerateEntries(sourcePath, "*", SearchOptions.Default))
    {
        string target = Path.Combine(destinationPath, entry.Name);
        if (entry.Type == DirectoryEntryType.Directory)
        {
            Directory.CreateDirectory(target);
            CopyDirectory(source, entry.FullPath, target);
        }
        else if (entry.Type == DirectoryEntryType.File)
        {
            CopyFile(source, entry, target);
        }
    }
}

static void CopyFile(IFileSystem source, DirectoryEntryEx entry, string destination)
{
    var file = new UniqueRef<IFile>();
    try
    {
        source.OpenFile(ref file.Ref, entry.FullPath.ToU8Span(), OpenMode.Read).ThrowIfFailure();
        using var output = new FileStream(destination, FileMode.Create, FileAccess.Write, FileShare.None, 1024 * 1024);
        byte[] buffer = new byte[1024 * 1024];
        long offset = 0;
        while (offset < entry.Size)
        {
            int length = (int)Math.Min(buffer.Length, entry.Size - offset);
            file.Get.Read(out long read, offset, buffer.AsSpan(0, length)).ThrowIfFailure();
            if (read != length) throw new EndOfStreamException(entry.FullPath);
            output.Write(buffer, 0, length);
            offset += length;
        }
    }
    finally
    {
        file.Destroy();
    }
}

sealed class OpenedNca : IDisposable
{
    private readonly LocalFileSystem _fileSystem;
    private readonly UniqueRef<IFile> _file;
    public Nca Nca { get; }
    public LibHac.Tools.FsSystem.NcaUtils.NcaHeader Header => Nca.Header;

    public OpenedNca(KeySet keySet, string path)
    {
        _fileSystem = new LocalFileSystem(Path.GetDirectoryName(Path.GetFullPath(path))!);
        _file = new UniqueRef<IFile>();
        try
        {
            _fileSystem.OpenFile(ref _file.Ref, ("/" + Path.GetFileName(path)).ToU8Span(), OpenMode.Read).ThrowIfFailure();
            Nca = new Nca(keySet, _file.Get.AsStorage());
        }
        catch
        {
            _file.Destroy();
            _fileSystem.Dispose();
            throw;
        }
    }

    public void Dispose()
    {
        _file.Destroy();
        _fileSystem.Dispose();
    }
}
