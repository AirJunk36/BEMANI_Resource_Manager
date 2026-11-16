using static IIDXResourceViewer.I18n;
using System.ComponentModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace IIDXResourceViewer;

public sealed class AnimationLayer : INotifyPropertyChanged
{
    public string Id { get; init; } = "";
    public string Parent { get; init; } = "";
    public string Name { get; init; } = "";
    public string Detail { get; init; } = "";
    public int Level { get; init; }
    public Thickness Indent => new(Math.Min(Level, 8) * 12, 0, 0, 0);
    private bool visible;
    public bool Visible { get => visible; set { if (visible != value) { visible = value; PropertyChanged?.Invoke(this, new(nameof(Visible))); } } }
    public event PropertyChangedEventHandler? PropertyChanged;
    private bool hasChildren, expanded = true;
    public Visibility GroupVisibility => hasChildren ? Visibility.Visible : Visibility.Collapsed;
    public string Chevron => expanded ? "\uE70D" : "\uE76C";
    public void GroupState(bool has, bool open)
    {
        if (hasChildren != has) { hasChildren = has; PropertyChanged?.Invoke(this, new(nameof(GroupVisibility))); }
        if (expanded != open) { expanded = open; PropertyChanged?.Invoke(this, new(nameof(Chevron))); }
    }
}

public sealed record AnimationLayersReply(List<AnimationLayer> Layers, bool Truncated, int Package_id, string? Selected = null, double[]? Bounds = null);

public sealed partial class MainWindow
{
    private List<AnimationLayer> layerRows = new();
    private AnimationLayersReply? layerReport;
    private bool layersBusy;
    private int? pickToken;
    private bool pickingLayer => pickToken == generation;
    private string? selectedLayerID;
    private double[]? selectedLayerBounds;
    private readonly HashSet<string> collapsedLayers = new();

    private void UpdateLayerGroups()
    {
        var parents = layerRows.Select(r => r.Parent).ToHashSet();
        foreach (var row in layerRows) row.GroupState(parents.Contains(row.Id), !collapsedLayers.Contains(row.Id));
    }

    private bool RowExpanded(AnimationLayer row)
    {
        string parent = row.Parent;
        while (parent.Length > 0) {
            if (collapsedLayers.Contains(parent)) return false;
            int slash = parent.LastIndexOf('/'); parent = slash < 0 ? "" : parent[..slash];
        }
        return true;
    }

    private void RevealLayer(string id)
    {
        string key = id;
        while (key.Length > 0) { collapsedLayers.Remove(key); int slash = key.LastIndexOf('/'); key = slash < 0 ? "" : key[..slash]; }
        LayerSearch.Text = Tr(""); UpdateLayerGroups(); FilterLayers();
        var row = layerRows.FirstOrDefault(r => r.Id == id);
        LayersList.SelectedItem = row;
        if (row is not null) LayersList.ScrollIntoView(row);
    }

    private void FilterLayers()
    {
        if (LayerSearch is null || LayersList is null) return;
        string query = LayerSearch.Text;
        LayersList.ItemsSource = layerRows.Where(r => query.Length == 0 ? RowExpanded(r) : r.Name.Contains(query, StringComparison.OrdinalIgnoreCase)
            || r.Detail.Contains(query, StringComparison.OrdinalIgnoreCase)).ToList();
        LayersList.SelectedItem = layerRows.FirstOrDefault(r => r.Id == selectedLayerID);
    }

    private void AcceptLayers(AnimationLayersReply reply)
    {
        layerReport = reply;
        if (layerRows.Select(r => r.Id).SequenceEqual(reply.Layers.Select(r => r.Id))) {
            for (int i = 0; i < layerRows.Count; i++) layerRows[i].Visible = reply.Layers[i].Visible;
        } else {
            layerRows = reply.Layers;
            UpdateLayerGroups();
            FilterLayers();
        }
        LayerStatus.Text = Tr(reply.Truncated ? "列表已截断；分组开关仍控制全部子素材" : $"{reply.Layers.Count} 项");
    }

    private async Task RefreshLayers()
    {
        if (animation is null || layersBusy) return;
        int token = generation, id = animation.Id;
        layersBusy = true;
        try {
            var reply = await Request<AnimationLayersReply>("animation_layers", new() { ["animation"] = id, ["frame"] = frame });
            if (token == generation && animation?.Id == id) AcceptLayers(reply);
        } catch (Exception ex) { LayerStatus.Text = Tr(ex.Message); }
        finally { layersBusy = false; }
    }

    private async Task SetLayer(string id, bool shown)
    {
        if (animation is null || layerReport is null || layersBusy) return;
        int token = generation, animationID = animation.Id;
        layersBusy = true; LayersList.IsEnabled = false;
        foreach (var control in LayerActions.Children.OfType<Panel>().SelectMany(p => p.Children).OfType<Control>()) control.IsEnabled = false;
        try {
            var reply = await Request<AnimationLayersReply>("set_layer_visible", new() {
                ["animation"] = animationID, ["layer"] = id, ["visible"] = shown, ["package_id"] = layerReport.Package_id });
            if (token != generation || animation?.Id != animationID) return;
            AcceptLayers(reply);
            if (selectedLayerID is not null && !layerRows.Any(r => r.Id == selectedLayerID && r.Visible)) ClearLayerSelection();
            generation++; rendering = false; pendingFrame = null;
            baseFrame = frame; startedAt = Now;
            await Render(frame);
        } catch (Exception ex) { LayerStatus.Text = Tr(ex.Message); }
        finally {
            layersBusy = false; LayersList.IsEnabled = true;
            foreach (var control in LayerActions.Children.OfType<Panel>().SelectMany(p => p.Children).OfType<Control>()) control.IsEnabled = true;
        }
    }

    private async void Layers_Opened(object sender, object e) => await RefreshLayers();
    private async void LayersRefresh_Click(object sender, RoutedEventArgs e) => await RefreshLayers();
    private void LayerSearch_Changed(object sender, TextChangedEventArgs e) => FilterLayers();
    private async void LayersAll_Click(object sender, RoutedEventArgs e) => await SetLayer("root", (sender as Button)?.Tag as string == "show");
    private async void Layer_Checked(object sender, RoutedEventArgs e)
    {
        if (!layersBusy && sender is CheckBox { DataContext: AnimationLayer row } box && (box.IsChecked == true) != row.Visible)
            await SetLayer(row.Id, box.IsChecked == true);
    }

    private void FoldLayer_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as Button)?.Tag is not string id) return;
        if (!collapsedLayers.Add(id)) collapsedLayers.Remove(id);
        UpdateLayerGroups(); FilterLayers();
        var row = layerRows.FirstOrDefault(r => r.Id == id); if (row is not null) LayersList.ScrollIntoView(row);
    }
    private void FoldAll_Click(object sender, RoutedEventArgs e)
    {
        collapsedLayers.Clear();
        if ((sender as Button)?.Tag as string == "collapse") foreach (var row in layerRows) if (row.Parent.Length > 0) collapsedLayers.Add(row.Parent);
        UpdateLayerGroups(); FilterLayers();
    }
    private void LayerSelection_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (LayersList.SelectedItem is AnimationLayer row && row.Id != selectedLayerID) {
            selectedLayerID = row.Id; selectedLayerBounds = null; UpdateLayerOutline();
        }
    }
    private void ClearLayerSelection()
    {
        selectedLayerID = null; selectedLayerBounds = null; LayersList.SelectedItem = null; UpdateLayerOutline();
    }
    private void UpdateLayerOutline()
    {
        if (selectedLayerBounds is not { Length: 4 } b) { SelectionOutline.Visibility = Visibility.Collapsed; return; }
        SelectionOutline.Visibility = Visibility.Visible;
        double width = double.IsFinite(PreviewImage.Width) ? PreviewImage.Width : PreviewImage.ActualWidth;
        double height = double.IsFinite(PreviewImage.Height) ? PreviewImage.Height : PreviewImage.ActualHeight;
        Canvas.SetLeft(SelectionOutline, b[0] * width); Canvas.SetTop(SelectionOutline, b[1] * height);
        SelectionOutline.Width = Math.Max(1, b[2] * width); SelectionOutline.Height = Math.Max(1, b[3] * height);
    }
    private async Task PickLayer(double u, double v)
    {
        if (animation is null || working || pickingLayer) return;
        Pause(); generation++; rendering = false; pendingFrame = null;
        int token = generation, id = animation.Id; pickToken = token;
        try {
            var reply = await Request<AnimationLayersReply>("pick_layer", new() { ["animation"] = id, ["frame"] = frame, ["u"] = u, ["v"] = v, ["renderer"] = RenderMode });
            if (token != generation || animation?.Id != id) return;
            selectedLayerID = reply.Selected; selectedLayerBounds = reply.Bounds;
            if (reply.Selected is string chosen) {
                AcceptLayers(reply); RevealLayer(chosen); LayersFlyout.ShowAt(LayersButton);
            } else { LayersList.SelectedItem = null; LayersFlyout.Hide(); }
            UpdateLayerOutline();
        } catch (Exception ex) { if (token == generation) ShowError(ex); }
        finally { if (pickToken == token) pickToken = null; }
    }
    private async void PreviewImage_Tapped(object sender, Microsoft.UI.Xaml.Input.TappedRoutedEventArgs e)
    {
        if (PreviewImage.ActualWidth <= 0 || PreviewImage.ActualHeight <= 0) return;
        var point = e.GetPosition(PreviewImage);
        await PickLayer(point.X / PreviewImage.ActualWidth, point.Y / PreviewImage.ActualHeight);
    }
}
