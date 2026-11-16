using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace IIDXResourceViewer;

public sealed partial class MainWindow
{
    private static IEnumerable<T> LiveControls<T>(DependencyObject root) where T:DependencyObject
    {
        if(root is T match)yield return match;
        for(int i=0;i<VisualTreeHelper.GetChildrenCount(root);i++)foreach(var item in LiveControls<T>(VisualTreeHelper.GetChild(root,i)))yield return item;
    }
    private async Task<Dictionary<string,object>> RunLiveSmoke(string data,string report)
    {
        string source=System.IO.Path.Combine(data,"live2d","rasis_ver7","rasis_ver7.model3.json");
        string moc=System.IO.Path.Combine(System.IO.Path.GetDirectoryName(source)!,"rasis_ver7.moc3");
        string hash=Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(moc)));
        await Open(source);await AwaitState(()=>package?.Format=="live2d"&&animation is not null&&!working&&!rendering&&PreviewImage.Source is not null,"Live2D preview");
        if(LiveEditorButton.Visibility!=Visibility.Visible||rendererInfo?.Hardware!=true)throw new Exception("Live2D native GPU preview unavailable");
        var texture=contents.First(c=>c.Type=="asset"&&package!.Assets.Any(a=>a.Id==c.Index&&a.Kind=="图片"));
        ContentsList.SelectedItem=texture;await AwaitState(()=>asset is not null&&!working&&previewKind=="image","Live2D texture selection");
        LiveEditor_Click(this,new RoutedEventArgs());
        await AwaitState(()=>liveEditor is not null&&LiveControls<ComboBox>(liveEditor).Any(c=>c.Items.Count==2&&c.Items[0] as string=="参数"),"Live2D simple tools");
        var pane=liveEditor!;
        if(animation is null)throw new Exception("Editor opened without a model preview");
        await AwaitState(()=>pane.Preview.ActualWidth>=300&&pane.Preview.ActualHeight>=300,"Live2D DPI layout",5);
        SmokeTrace($"Live2D pane {pane.ActualWidth}x{pane.ActualHeight}, preview {pane.Preview.ActualWidth}x{pane.Preview.ActualHeight}, scale {pane.XamlRoot?.RasterizationScale}");
        var mode=LiveControls<ComboBox>(pane).First(c=>c.Items.Count==2&&c.Items[0] as string=="简易模式");
        var tabs=LiveControls<ComboBox>(pane).First(c=>c.Items.Count==2&&c.Items[0] as string=="参数");
        if(tabs.Items.Cast<object>().Any(x=>x as string=="网格"))throw new Exception("Professional tools leaked into simple mode");
        string simple=System.IO.Path.Combine(report,"live2d-simple.png");File.WriteAllBytes(simple,Array.Empty<byte>());await CapturePreview(simple,pane);
        mode.SelectedIndex=1;
        if(!tabs.Items.Cast<object>().Any(x=>x as string=="网格")||!tabs.Items.Cast<object>().Any(x=>x as string=="变形器")||!tabs.Items.Cast<object>().Any(x=>x as string=="动作/物理"))throw new Exception("Professional tools missing");
        tabs.SelectedIndex=2;await AwaitState(()=>LiveControls<Canvas>(pane).Any(c=>c.Children.Count>0),"Live2D mesh controls");
        string pro=System.IO.Path.Combine(report,"live2d-professional.png");File.WriteAllBytes(pro,Array.Empty<byte>());await CapturePreview(pro,pane);
        tabs.SelectedIndex=3;await AwaitState(()=>LiveControls<NumberBox>(pane).Any(n=>n.Header as string=="角度"),"Live2D deformer controls");
        var info=await Request<LiveInfo>("live2d_info");var parameter=info.Parameters.First();double value=parameter.Value==parameter.Minimum?parameter.Maximum:parameter.Minimum;
        await Request<LiveInfo>("live2d_edit",new(){["operation"]="parameter",["index"]=parameter.Id,["value"]=value});
        var selected=package!.Animations.First(a=>a.Name=="Idle");
        ContentsList.SelectedItem=contents.First(c=>c.Type=="animation"&&c.Index==selected.Id);
        await AwaitState(()=>animation?.Id==selected.Id&&!rendering,"Live2D motion selection");await Render(30);
        string target=System.IO.Path.Combine(report,"live2d-export-"+Guid.NewGuid().ToString("N"));
        await Request<JsonElement>("live2d_save",new(){["target"]=target});
        liveEditorWindow!.Close();await AwaitState(()=>liveEditor is null,"Live2D editor close");
        await Open(System.IO.Path.Combine(target,"rasis_ver7.model3.json"));await AwaitState(()=>package?.Format=="live2d"&&animation is not null&&!rendering,"Live2D exported model");
        var reopened=await Request<LiveInfo>("live2d_info");if(Math.Abs(reopened.Parameters[parameter.Id].Default-value)>.0001)throw new Exception("Live2D saved defaults changed");
        if(hash!=Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(moc))))throw new Exception("Live2D source changed");
        Directory.Delete(target,true);
        return new(){["simple_professional_modes"]=true,["mesh_deformer_tools"]=true,["real_time_gpu"]=true,["export_roundtrip"]=true,["sources_unchanged"]=true};
    }
    private async Task LiveSelfTest()
    {
        string report=Environment.GetEnvironmentVariable("IIDX_TEST_REPORT_DIR")??System.IO.Path.GetTempPath();
        string data=Environment.GetEnvironmentVariable("IIDX_GAME_ROOT")??"";
        Dictionary<string,object> checks;
        try{checks=await RunLiveSmoke(data,report);checks["ok"]=true;}
        catch(Exception e){checks=new(){["ok"]=false,["error"]=e.ToString(),["status"]=Status.Text};}
        File.WriteAllText(System.IO.Path.Combine(report,"live2d-smoke.json"),JsonSerializer.Serialize(checks,new JsonSerializerOptions{WriteIndented=true}));
        Close();
    }
}
