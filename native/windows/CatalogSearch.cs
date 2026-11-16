namespace IIDXResourceViewer;

// Immutable text index; only completed searches update the small refinement cache.
internal sealed class CatalogSearch
{
    private sealed record Row(FileEntry File, string Text);
    public readonly List<FileEntry> Source;
    private readonly Row[] rows;
    private readonly object gate = new();
    private string previousQuery = "", previousVersion = "", previousKind = "";
    private List<Row> previousRows = new();
    private List<FileEntry> previousResult = new();
    public CatalogSearch(List<FileEntry> source, CancellationToken token) {
        Source = source; rows = new Row[source.Count];
        for (int i = 0; i < source.Count; i++) {
            if ((i & 255) == 0) token.ThrowIfCancellationRequested();
            var file = source[i]; rows[i] = new(file,(file.Relative + " " + file.Song_title).ToUpperInvariant());
        }
    }
    public List<FileEntry> Search(string version, string kind, string query, CancellationToken token) {
        var terms = query.ToUpperInvariant().Split((char[]?)null,StringSplitOptions.RemoveEmptyEntries);
        query = string.Join(' ',terms);
        lock (gate) {
            token.ThrowIfCancellationRequested();
            bool sameFilters = version == previousVersion && kind == previousKind;
            if (sameFilters && query == previousQuery) return previousResult;
            IEnumerable<Row> candidates = sameFilters && previousQuery.Length > 0 && query.StartsWith(previousQuery,StringComparison.Ordinal) ? previousRows : rows;
            var matches = new List<Row>(); var result = new List<FileEntry>(); int count = 0;
            foreach (var row in candidates) {
                if ((count++ & 255) == 0) token.ThrowIfCancellationRequested();
                if ((version != "全部版本" && row.File.Version != version) || (kind != "所有类型" && row.File.Kind != kind)) continue;
                bool found = true;
                foreach (var term in terms) if (!row.Text.Contains(term,StringComparison.Ordinal)) { found = false; break; }
                if (found) { matches.Add(row); result.Add(row.File); }
            }
            token.ThrowIfCancellationRequested();
            previousRows = matches; previousResult = result; previousQuery = query; previousVersion = version; previousKind = kind;
            return result;
        }
    }
}
