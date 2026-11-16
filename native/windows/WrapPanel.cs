using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Foundation;

namespace IIDXResourceViewer;

public sealed class WrapPanel : Panel
{
    public double Spacing { get; set; } = 6;

    protected override Size MeasureOverride(Size availableSize)
    {
        foreach (var child in Children) child.Measure(new Size(availableSize.Width, double.PositiveInfinity));
        return Layout(availableSize.Width, false);
    }

    protected override Size ArrangeOverride(Size finalSize)
    {
        Layout(finalSize.Width, true);
        return finalSize;
    }

    private Size Layout(double availableWidth, bool arrange)
    {
        double x = 0, y = 0, rowHeight = 0, width = 0;
        foreach (var child in Children) {
            if (child.Visibility == Visibility.Collapsed) continue;
            double childWidth = Math.Min(availableWidth, child.DesiredSize.Width);
            if (x > 0 && x + childWidth > availableWidth) {
                y += rowHeight + Spacing; x = 0; rowHeight = 0;
            }
            if (arrange) child.Arrange(new Rect(x, y, childWidth, child.DesiredSize.Height));
            width = Math.Max(width, x + childWidth);
            x += childWidth + Spacing;
            rowHeight = Math.Max(rowHeight, child.DesiredSize.Height);
        }
        return new Size(width, y + rowHeight);
    }
}
