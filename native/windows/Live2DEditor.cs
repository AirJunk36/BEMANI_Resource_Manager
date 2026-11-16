using static IIDXResourceViewer.I18n;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Microsoft.UI.Xaml.Shapes;
using Windows.Foundation;
using Windows.Storage.Pickers;

namespace IIDXResourceViewer;

public sealed record LiveParameter(int Id, string Key, string Name, double Minimum, double Maximum, double Value, double Default, bool Common);
public sealed record LiveRow(int Id, string Name);
public sealed record LiveInfo(int Version, List<LiveParameter> Parameters, Dictionary<string,List<LiveRow>> Sections, List<string> Textures, List<string> Documents, int Revision, bool Can_undo, bool Can_redo);

// Native editor shares the renderer and transport with the main preview.
internal sealed class LiveEditorPane : Grid
{
    private readonly Func<string,Dictionary<string,object>?,Task<JsonElement>> request;
    private readonly Func<Task> refresh;
    private readonly Action<object> picker;
    private readonly Action play;
    private readonly Action<int> selectMotion;
    private readonly string source;
    private LiveInfo? info;
    private JsonElement details;
    private readonly ComboBox mode = new() { ItemsSource=new[]{"简易模式","专业模式"},SelectedIndex=0,Width=160 };
    private readonly ComboBox tabs = new() { ItemsSource=new[]{"参数","贴图"},SelectedIndex=0,HorizontalAlignment=HorizontalAlignment.Stretch };
    private readonly StackPanel tools = new() { Spacing=12 };
    private readonly TextBlock status = new() { FontSize=12,Text=Tr("修改后导出为新模型"),TextWrapping=TextWrapping.Wrap };
    private readonly Button undo = new() { Content=Tr("撤销" )}, redo = new() { Content=Tr("重做" )};
    private readonly ComboBox elements = new() { DisplayMemberPath="Name",HorizontalAlignment=HorizontalAlignment.Stretch };
    private readonly NumberBox forms = new() { Header=Tr("关键形"),Minimum=1,Maximum=1,Value=1,SpinButtonPlacementMode=NumberBoxSpinButtonPlacementMode.Compact };
    private readonly CheckBox uv = new() { Content=Tr("UV" )};
    private readonly Canvas canvas = new() { Height=240,Background=new SolidColorBrush(Windows.UI.Color.FromArgb(16,128,128,128)) };
    private List<Point> points=new();
    private List<int[]> triangles=new();
    private string section="meshes";
    private int selected,form,vertex,detailGeneration;
    private bool updating,editing,closed;
    private Dictionary<string,object>? pending,queued;
    private Task? queueTask;
    private int? dragged;
    private (double X,double Y,double Size)? dragBounds;
    private string document="";
    private readonly TextBox json = new() { AcceptsReturn=true,TextWrapping=TextWrapping.NoWrap,MinHeight=320,FontFamily=new FontFamily("Consolas") };
    private readonly Slider timeline = new() {Minimum=0,Maximum=1};
    private bool syncingTimeline;
    public readonly Image Preview = new() { Stretch=Stretch.Uniform,HorizontalAlignment=HorizontalAlignment.Stretch,VerticalAlignment=VerticalAlignment.Stretch };
    private bool Professional => mode.SelectedIndex==1;
    private JsonElement Record => Property(details,"record");
    private JsonElement Owner => Property(details,"owner");
    private string EditSection => Property(details,"section").GetString() ?? section;
    private int EditIndex => (int)Number(details,"index",selected);
    private static JsonElement Property(JsonElement root,string key) => root.ValueKind==JsonValueKind.Object && root.TryGetProperty(key,out var p) ? p : default;
    private static double Number(JsonElement root,string key,double fallback=0) => Property(root,key).TryNumber(fallback);
    public LiveEditorPane(string source,List<AnimationItem> animations,int animationId,Func<string,Dictionary<string,object>?,Task<JsonElement>> request,
        Func<Task> refresh,Action<object> picker,Action play,Action<int> selectMotion,Action<int> seek)
    {
        this.source=source;this.request=request;this.refresh=refresh;this.picker=picker;this.play=play;this.selectMotion=selectMotion;
        Loaded+=(_,_)=>I18n.Apply(this);
        Background=(Brush)Application.Current.Resources["ApplicationPageBackgroundThemeBrush"];
        RowDefinitions.Add(new(){Height=GridLength.Auto});RowDefinitions.Add(new(){Height=new GridLength(1,GridUnitType.Star)});RowDefinitions.Add(new(){Height=GridLength.Auto});
        var header=new StackPanel { Orientation=Orientation.Horizontal,Spacing=8,Margin=new Thickness(16) };
        header.Children.Add(new TextBlock {Text=Tr("Live2D 编辑"),FontSize=22,VerticalAlignment=VerticalAlignment.Center});header.Children.Add(mode);header.Children.Add(undo);header.Children.Add(redo);
        header.Children.Add(Button("导出模型…",Save));Children.Add(header);
        var content=new Grid {ColumnSpacing=12,Margin=new Thickness(16,0,16,12)};content.ColumnDefinitions.Add(new(){Width=new GridLength(1,GridUnitType.Star)});content.ColumnDefinitions.Add(new(){Width=new GridLength(460)});SetRow(content,1);Children.Add(content);
        var preview=new Grid();preview.RowDefinitions.Add(new(){Height=new GridLength(1,GridUnitType.Star)});preview.RowDefinitions.Add(new(){Height=GridLength.Auto});preview.RowDefinitions.Add(new(){Height=GridLength.Auto});preview.Children.Add(Preview);
        var controls=new StackPanel { Orientation=Orientation.Horizontal,Spacing=8,Margin=new Thickness(0,10,0,0) };
        var motions=new ComboBox { ItemsSource=animations,DisplayMemberPath="Name",SelectedItem=animations.FirstOrDefault(a=>a.Id==animationId),MinWidth=200 };
        motions.SelectionChanged+=(_,_)=>{if(motions.SelectedItem is AnimationItem a)selectMotion(a.Id);};
        controls.Children.Add(motions);controls.Children.Add(Button("播放 / 暂停",()=>{play();return Task.CompletedTask;}));
        controls.Children.Add(Button("跟随动作",async()=>{await Flush();await Edit(new(){["operation"]="release"},false);BuildTools();}));
        preview.Children.Add(controls);SetRow(controls,1);content.Children.Add(preview);
        SetRow(timeline,2);preview.Children.Add(timeline);timeline.ValueChanged+=(_,e)=>{if(!syncingTimeline)seek((int)e.NewValue);};
        var right=new Grid { RowSpacing=12 };right.RowDefinitions.Add(new(){Height=GridLength.Auto});right.RowDefinitions.Add(new(){Height=new GridLength(1,GridUnitType.Star)});
        right.Children.Add(tabs);var scroll=new ScrollViewer {Content=Tr(tools),VerticalScrollBarVisibility=ScrollBarVisibility.Auto};SetRow(scroll,1);right.Children.Add(scroll);SetColumn(right,1);content.Children.Add(right);
        SetRow(status,2);status.Margin=new Thickness(16,0,16,14);Children.Add(status);
        mode.SelectionChanged+=(_,_)=>{tabs.ItemsSource=Professional?new[]{"参数","贴图","网格","变形器","动作/物理"}:new[]{"参数","贴图"};tabs.SelectedIndex=0;BuildTools();};
        tabs.SelectionChanged+=(_,_)=>BuildTools();
        undo.Click+=async(_,_)=>await Undo(false);redo.Click+=async(_,_)=>await Undo(true);
        elements.SelectionChanged+=async(_,_)=>{if(!updating&&elements.SelectedItem is LiveRow row){selected=row.Id;form=0;await LoadDetails();}};
        forms.ValueChanged+=async(_,_)=>{if(!updating&&double.IsFinite(forms.Value)){form=(int)forms.Value-1;await LoadDetails();}};
        uv.Checked+=async(_,_)=>{if(!updating)await LoadDetails();};uv.Unchecked+=async(_,_)=>{if(!updating)await LoadDetails();};
        canvas.SizeChanged+=(_,_)=>DrawCanvas();canvas.PointerPressed+=CanvasPressed;canvas.PointerMoved+=CanvasMoved;canvas.PointerReleased+=CanvasReleased;canvas.PointerCanceled+=CanvasReleased;
        Preview.Tapped+=async(_,e)=>{
            if(!Professional||Preview.Source is not BitmapSource image)return;
            var p=e.GetPosition(Preview);double scale=Math.Min(Preview.ActualWidth/image.PixelWidth,Preview.ActualHeight/image.PixelHeight);
            double u=(p.X-(Preview.ActualWidth-image.PixelWidth*scale)/2)/(image.PixelWidth*scale),v=(p.Y-(Preview.ActualHeight-image.PixelHeight*scale)/2)/(image.PixelHeight*scale);
            try {
                var hit=await request("pick_layer",new(){["animation"]=((AnimationItem?)motions.SelectedItem)?.Id??0,["u"]=u,["v"]=v});
                string? key=Property(hit,"selected").GetString();
                if(key is not null&&int.TryParse(key.Split(':').Last(),out int index)){
                    tabs.SelectedIndex=2;elements.SelectedItem=info?.Sections["meshes"].FirstOrDefault(r=>r.Id==index);
                }
            }catch(Exception ex){status.Text=Tr(ex.Message);}
        };
    }
    private static Button Button(string label,Func<Task> action) { var b=new Button {Content=Tr(label)};b.Click+=async(_,_)=>await action();return b; }
    public async Task Load()
    {
        try { info=Deserialize<LiveInfo>(await request("live2d_info",null));BuildTools();UpdateUndo(); }
        catch(Exception e){status.Text=Tr(e.Message);}
    }
    private static T Deserialize<T>(JsonElement json)=>JsonSerializer.Deserialize<T>(json.GetRawText(),new JsonSerializerOptions{PropertyNameCaseInsensitive=true})!;
    public void Sync(ImageSource? image,int frame,int length)
    {
        Preview.Source=image;syncingTimeline=true;timeline.Maximum=Math.Max(1,length-1);timeline.Value=frame;syncingTimeline=false;
    }
    private void UpdateUndo(){undo.IsEnabled=info?.Can_undo==true;redo.IsEnabled=info?.Can_redo==true;}
    private async Task Edit(Dictionary<string,object> args,bool reload=true)
    {
        if(closed)return;
        if(editing){pending=args;return;}editing=true;
        try { info=Deserialize<LiveInfo>(await request("live2d_edit",args));UpdateUndo();if(reload&&tabs.SelectedIndex is 2 or 3)await LoadDetails();await refresh();status.Text=Tr("修改后导出为新模型"); }
        catch(Exception e){status.Text=Tr(e.Message);}
        finally{editing=false;}
        if(pending is {} next){pending=null;await Edit(next,reload);}
    }
    private void Queue(Dictionary<string,object> args)
    {
        queued=args;if(queueTask is not null)return;
        queueTask=RunQueue();
    }
    private async Task RunQueue()
    {
        while(queued is not null&&!closed){await Task.Delay(60);if(queued is {} args){queued=null;await Edit(args,false);}}
        queueTask=null;
    }
    public async Task Flush(){if(queueTask is {} task)await task;while(editing)await Task.Delay(15);}
    public void Close(){closed=true;}
    private async Task Undo(bool forward)
    {
        await Flush();await Edit(new(){["operation"]=forward?"redo":"undo"});BuildTools();
    }
    private void BuildTools()
    {
        if(info is null||tabs.SelectedIndex<0)return;
        tools.Children.Clear();updating=true;
        if(tabs.SelectedIndex==0)BuildParameters();
        else if(tabs.SelectedIndex==1)BuildTextures();
        else if(tabs.SelectedIndex==4)BuildDocuments();
        else {
            section=tabs.SelectedIndex==2?"meshes":"deformers";selected=0;form=0;uv.IsChecked=false;
            elements.ItemsSource=info.Sections.GetValueOrDefault(section)??new();elements.SelectedIndex=0;
            tools.Children.Add(elements);tools.Children.Add(forms);if(section=="meshes")tools.Children.Add(uv);
            tools.Children.Add(geometryTools);_ = LoadDetails();
        }
        updating=false;
    }
    private void BuildParameters()
    {
        if(info is null)return;
        var filter=new TextBox{PlaceholderText=Tr("搜索参数")};var list=new StackPanel{Spacing=14};
        if(Professional)tools.Children.Add(filter);tools.Children.Add(list);
        void Populate()
        {
            list.Children.Clear();var common=info.Parameters.Where(p=>p.Common).ToList();
            var rows=Professional?info.Parameters.Where(p=>filter.Text.Length==0||p.Name.Contains(filter.Text,StringComparison.OrdinalIgnoreCase)||p.Key.Contains(filter.Text,StringComparison.OrdinalIgnoreCase)):(common.Count>0?common:info.Parameters).Take(18);
            foreach(var p in rows){
                var row=new StackPanel{Spacing=4};row.Children.Add(new TextBlock{Text=p.Name,TextTrimming=TextTrimming.CharacterEllipsis});
                var slider=new Slider{Minimum=p.Minimum,Maximum=Math.Max(p.Minimum+.00001,p.Maximum),Value=p.Value,StepFrequency=Math.Max(.00001,(p.Maximum-p.Minimum)/1000)};
                var value=new NumberBox{Value=p.Value,Minimum=p.Minimum,Maximum=p.Maximum,Width=95};
                var controls=new Grid{ColumnSpacing=8};controls.ColumnDefinitions.Add(new(){Width=new GridLength(1,GridUnitType.Star)});controls.ColumnDefinitions.Add(new(){Width=GridLength.Auto});controls.Children.Add(slider);SetColumn(value,1);controls.Children.Add(value);row.Children.Add(controls);
                bool syncing=false;
                void Set(double v){if(syncing||!double.IsFinite(v))return;syncing=true;slider.Value=v;value.Value=v;syncing=false;Queue(new(){["operation"]="parameter",["index"]=p.Id,["value"]=v});}
                slider.ValueChanged+=(_,e)=>Set(e.NewValue);value.ValueChanged+=(_,e)=>Set(e.NewValue);
                row.Children.Add(Button("重置",()=>{Set(p.Default);return Task.CompletedTask;}));
                if(Professional){var range=new StackPanel{Spacing=6};range.Children.Add(Numeric("最小",p.Minimum,v=>Edit(new(){["operation"]="field",["section"]="parameters",["index"]=p.Id,["field"]="minimum",["value"]=v},false)));range.Children.Add(Numeric("最大",p.Maximum,v=>Edit(new(){["operation"]="field",["section"]="parameters",["index"]=p.Id,["field"]="maximum",["value"]=v},false)));row.Children.Add(new Expander{Header=Tr("范围"),Content=Tr(range),HorizontalAlignment=HorizontalAlignment.Stretch});}
                list.Children.Add(row);
            }
        }
        filter.TextChanged+=(_,_)=>Populate();Populate();
    }
    private void BuildTextures()
    {
        if(info is null)return;
        for(int i=0;i<info.Textures.Count;i++){int index=i;var row=new StackPanel{Spacing=6};row.Children.Add(new TextBlock{Text=info.Textures[i],TextWrapping=TextWrapping.Wrap});row.Children.Add(Button("替换…",async()=>{
            var choose=new FileOpenPicker();picker(choose);choose.FileTypeFilter.Add(".png");choose.FileTypeFilter.Add(".jpg");var file=await choose.PickSingleFileAsync();if(file is not null)await Edit(new(){["operation"]="texture",["index"]=index,["path"]=file.Path},false);
        }));tools.Children.Add(row);}
    }
    private void BuildDocuments()
    {
        if(info is null)return;
        var names=info.Documents.Where(n=>!n.EndsWith(".model3.json")).ToList();var choose=new ComboBox{ItemsSource=names,HorizontalAlignment=HorizontalAlignment.Stretch};
        document=names.FirstOrDefault(n=>n.EndsWith(".motion3.json"))??names.FirstOrDefault()??"";choose.SelectedItem=document;
        async Task LoadJson(){try {json.Text=Property(await request("live2d_details",new(){["section"]="json",["document"]=document}),"text").GetString()??"";}catch(Exception e){status.Text=Tr(e.Message);}}
        choose.SelectionChanged+=async(_,_)=>{document=choose.SelectedItem as string??"";await LoadJson();};tools.Children.Add(choose);tools.Children.Add(json);
        tools.Children.Add(Button("应用",()=>Edit(new(){["operation"]="json",["document"]=document,["text"]=json.Text},false)));_ = LoadJson();
    }
    private readonly StackPanel geometryTools=new(){Spacing=10};
    private async Task LoadDetails()
    {
        if(info is null)return;int token=++detailGeneration;
        try {
            var result=await request("live2d_details",new(){["section"]=uv.IsChecked==true&&section=="meshes"?"uvs":section,["index"]=selected,["form"]=form});
            if(token!=detailGeneration||closed)return;details=result;updating=true;
            forms.Maximum=Math.Max(1,Number(details,"form_count",1));forms.Value=form+1;updating=false;
            points=Property(details,"points").ValueKind==JsonValueKind.Array?Property(details,"points").EnumerateArray().Select(p=>new Point(p[0].GetDouble(),p[1].GetDouble())).ToList():new();
            triangles=Property(details,"triangles").ValueKind==JsonValueKind.Array?Deserialize<List<int[]>>(Property(details,"triangles")):new();vertex=Math.Min(vertex,Math.Max(0,points.Count-1));
            BuildGeometry();
        }catch(Exception e){status.Text=Tr(e.Message);updating=false;}
    }
    private FrameworkElement Numeric(string label,double value,Func<double,Task> apply)
    {
        var row=new Grid{ColumnSpacing=6};row.ColumnDefinitions.Add(new(){Width=new GridLength(1,GridUnitType.Star)});row.ColumnDefinitions.Add(new(){Width=GridLength.Auto});
        var number=new NumberBox{Header=Tr(label),Value=value,HorizontalAlignment=HorizontalAlignment.Stretch};row.Children.Add(number);var button=Button("应用",async()=>{if(double.IsFinite(number.Value))await apply(number.Value);});button.VerticalAlignment=VerticalAlignment.Bottom;SetColumn(button,1);row.Children.Add(button);return row;
    }
    private Task Field(string key,double value,bool keyform=false)=>Edit(new(){["operation"]="field",["section"]=keyform?Property(details,"form_section").GetString()??EditSection:EditSection,["index"]=keyform?(int)Number(details,"form_index",EditIndex):EditIndex,["field"]=key,["value"]=value});
    private void BuildGeometry()
    {
        geometryTools.Children.Clear();
        var keys=Property(details,"keys");
        if(keys.ValueKind==JsonValueKind.Array&&keys.GetArrayLength()>0){
            geometryTools.Children.Add(new TextBlock {Text=Tr(string.Join(" · ",keys.EnumerateArray().Select(k=>$"{Property(k,"key").GetString()} = {Number(k,"value"):0.###}"))),TextWrapping=TextWrapping.Wrap,FontSize=12});
            geometryTools.Children.Add(Button("预览关键形",async()=>{await Flush();await Edit(new(){["operation"]="pose",["keys"]=Deserialize<List<Dictionary<string,JsonElement>>>(keys)},false);}));
        }
        if(points.Count>0){
            geometryTools.Children.Add(canvas);DrawCanvas();
            var index=new NumberBox{Header=Tr("顶点"),Minimum=1,Maximum=points.Count,Value=vertex+1,SpinButtonPlacementMode=NumberBoxSpinButtonPlacementMode.Compact};index.ValueChanged+=(_,_)=>{if(!double.IsFinite(index.Value))return;vertex=(int)index.Value-1;BuildGeometry();};geometryTools.Children.Add(index);
            var p=points[vertex];geometryTools.Children.Add(Numeric("X",p.X,v=>Move(vertex,new(v,p.Y))));geometryTools.Children.Add(Numeric("Y",p.Y,v=>Move(vertex,new(p.X,v))));
            var transform=new StackPanel{Spacing=6};var x=new NumberBox{Header=Tr("X"),Value=0};var y=new NumberBox{Header=Tr("Y"),Value=0};var scale=new NumberBox{Header=Tr("缩放"),Value=1};var angle=new NumberBox{Header=Tr("旋转"),Value=0};foreach(var c in new[]{x,y,scale,angle})transform.Children.Add(c);
            transform.Children.Add(Button("应用",()=>Edit(new(){["operation"]="transform",["section"]=EditSection,["index"]=EditIndex,["form"]=form,["dx"]=x.Value,["dy"]=y.Value,["scale"]=scale.Value,["angle"]=angle.Value})));geometryTools.Children.Add(new Expander{Header=Tr("变换"),Content=Tr(transform),HorizontalAlignment=HorizontalAlignment.Stretch});
            if(uv.IsChecked!=true){var key=Property(details,"form_record");geometryTools.Children.Add(Numeric("不透明度",Number(key,"opacity",1),v=>Field("opacity",v,true)));if(section=="meshes")geometryTools.Children.Add(Numeric("绘制顺序",Number(key,"order"),v=>Field("order",v,true)));}
            if(triangles.Count>0){var triangle=new NumberBox{Header=Tr("三角形"),Minimum=1,Maximum=triangles.Count,Value=1};var indices=new TextBox{Header=Tr("顶点编号（从 1 开始）"),Text=Tr(string.Join(", ",triangles[0].Select(v=>v+1)))};triangle.ValueChanged+=(_,_)=>{if(double.IsFinite(triangle.Value)&&triangle.Value>=1&&triangle.Value<=triangles.Count)indices.Text=Tr(string.Join(", ",triangles[(int)triangle.Value-1].Select(v=>v+1)));};var panel=new StackPanel{Spacing=6};panel.Children.Add(triangle);panel.Children.Add(indices);panel.Children.Add(Button("应用",async()=>{try{await Edit(new(){["operation"]="triangle",["index"]=selected,["triangle"]=(int)triangle.Value-1,["vertices"]=indices.Text.Split(new[]{',',' '},StringSplitOptions.RemoveEmptyEntries).Select(s=>int.Parse(s)-1).ToArray()});}catch(Exception e){status.Text=Tr(e.Message);}}));geometryTools.Children.Add(new Expander{Header=Tr("三角形"),Content=Tr(panel),HorizontalAlignment=HorizontalAlignment.Stretch});}
        }else if(EditSection=="rotation_forms"){
            foreach(var (field,label) in new[]{("angle","角度"),("x","X"),("y","Y"),("scale","缩放"),("opacity","不透明度")})geometryTools.Children.Add(Numeric(label,Number(Record,field),v=>Field(field,v)));
        }
        var parents=new List<LiveRow>{new(-1,"无")};parents.AddRange((info?.Sections.GetValueOrDefault("deformers")??new()).Where(r=>section!="deformers"||r.Id!=selected));
        var parent=new ComboBox{Header=Tr("父变形器"),ItemsSource=parents,DisplayMemberPath="Name",SelectedItem=parents.FirstOrDefault(r=>r.Id==(int)Number(Owner,"parent",-1)),HorizontalAlignment=HorizontalAlignment.Stretch};
        parent.SelectionChanged+=async(_,_)=>{if(parent.SelectedItem is LiveRow r)await Edit(new(){["operation"]="parent",["section"]=section,["index"]=selected,["parent"]=r.Id});};geometryTools.Children.Add(parent);
        if(section=="meshes"){
            var texture=new ComboBox{Header=Tr("贴图"),ItemsSource=info?.Textures,SelectedIndex=(int)Number(Owner,"texture"),HorizontalAlignment=HorizontalAlignment.Stretch};
            texture.SelectionChanged+=async(_,_)=>{if(texture.SelectedIndex>=0)await Edit(new(){["operation"]="field",["section"]="meshes",["index"]=selected,["field"]="texture",["value"]=texture.SelectedIndex});};geometryTools.Children.Add(texture);
            int flags=(int)Number(Owner,"flags");
            var blend=new ComboBox{Header=Tr("混合"),ItemsSource=new[]{"正常","加法","正片叠底"},SelectedIndex=Math.Min(2,flags&3),HorizontalAlignment=HorizontalAlignment.Stretch};
            blend.SelectionChanged+=async(_,_)=>{if(blend.SelectedIndex>=0)await Edit(new(){["operation"]="field",["section"]="meshes",["index"]=selected,["field"]="flags",["value"]=(flags&12)|blend.SelectedIndex});};geometryTools.Children.Add(blend);
            var inverse=new CheckBox{Content=Tr("反向剪裁"),IsChecked=(flags&8)!=0};
            inverse.Checked+=async(_,_)=>await Edit(new(){["operation"]="field",["section"]="meshes",["index"]=selected,["field"]="flags",["value"]=(flags&7)|8});
            inverse.Unchecked+=async(_,_)=>await Edit(new(){["operation"]="field",["section"]="meshes",["index"]=selected,["field"]="flags",["value"]=flags&7});geometryTools.Children.Add(inverse);
            var visible=new CheckBox{Content=Tr("显示"),IsChecked=Number(Owner,"visible",1)!=0};visible.Checked+=async(_,_)=>await Edit(new(){["operation"]="field",["section"]="meshes",["index"]=selected,["field"]="visible",["value"]=1});visible.Unchecked+=async(_,_)=>await Edit(new(){["operation"]="field",["section"]="meshes",["index"]=selected,["field"]="visible",["value"]=0});geometryTools.Children.Add(visible);
        }
        geometryTools.Children.Add(new TextBlock{Text=Tr("编辑现有网格与变形器的关键形"),FontSize=12});
    }
    private (double X,double Y,double Size) Bounds()
    {
        if(dragBounds is {} b)return b;if(points.Count==0)return(0,0,1);
        double x0=points.Min(p=>p.X),x1=points.Max(p=>p.X),y0=points.Min(p=>p.Y),y1=points.Max(p=>p.Y);return((x0+x1)/2,(y0+y1)/2,Math.Max(.01,Math.Max(x1-x0,y1-y0))*1.2);
    }
    private Point Screen(Point p){var b=Bounds();double scale=Math.Min(Math.Max(1,canvas.ActualWidth),240)/b.Size;return new(canvas.ActualWidth/2+(p.X-b.X)*scale,120-(p.Y-b.Y)*scale);}
    private Point Local(Point p){var b=Bounds();double scale=Math.Min(Math.Max(1,canvas.ActualWidth),240)/b.Size;return new(b.X+(p.X-canvas.ActualWidth/2)/scale,b.Y-(p.Y-120)/scale);}
    private void DrawCanvas()
    {
        canvas.Children.Clear();if(points.Count==0)return;
        void Line(int a,int b){if(a<0||b<0||a>=points.Count||b>=points.Count)return;var p=Screen(points[a]);var q=Screen(points[b]);canvas.Children.Add(new Line{X1=p.X,Y1=p.Y,X2=q.X,Y2=q.Y,Stroke=new SolidColorBrush(Windows.UI.Color.FromArgb(140,128,128,128)),StrokeThickness=.7});}
        foreach(var t in triangles){Line(t[0],t[1]);Line(t[1],t[2]);Line(t[2],t[0]);}
        int rows=(int)Number(Record,"rows"),cols=(int)Number(Record,"columns");
        if(triangles.Count==0&&(rows+1)*(cols+1)==points.Count){for(int y=0;y<=rows;y++)for(int x=0;x<=cols;x++){int i=y*(cols+1)+x;if(x<cols)Line(i,i+1);if(y<rows)Line(i,i+cols+1);}}
        for(int i=0;i<points.Count;i++){var p=Screen(points[i]);double r=i==vertex?4:2.5;var dot=new Ellipse{Width=r*2,Height=r*2,Fill=new SolidColorBrush(i==vertex?Microsoft.UI.Colors.Orange:Microsoft.UI.Colors.DodgerBlue)};Canvas.SetLeft(dot,p.X-r);Canvas.SetTop(dot,p.Y-r);canvas.Children.Add(dot);}
    }
    private void CanvasPressed(object sender,PointerRoutedEventArgs e)
    {
        if(points.Count==0)return;var p=e.GetCurrentPoint(canvas).Position;int nearest=Enumerable.Range(0,points.Count).MinBy(i=>Distance(Screen(points[i]),p));if(Distance(Screen(points[nearest]),p)>18)return;vertex=nearest;dragged=nearest;dragBounds=Bounds();canvas.CapturePointer(e.Pointer);DrawCanvas();e.Handled=true;
    }
    private static double Distance(Point a,Point b)=>Math.Sqrt(Math.Pow(a.X-b.X,2)+Math.Pow(a.Y-b.Y,2));
    private void CanvasMoved(object sender,PointerRoutedEventArgs e)
    {
        if(dragged is not int i)return;var p=Local(e.GetCurrentPoint(canvas).Position);points[i]=p;DrawCanvas();Queue(PointArgs(i,p));
    }
    private async void CanvasReleased(object sender,PointerRoutedEventArgs e)
    {
        if(dragged is not int i)return;var p=points[i];dragged=null;dragBounds=null;canvas.ReleasePointerCapture(e.Pointer);await Flush();await Move(i,p);
    }
    private Dictionary<string,object> PointArgs(int i,Point p)=>new(){["operation"]="point",["section"]=EditSection,["index"]=EditIndex,["form"]=form,["vertex"]=i,["x"]=p.X,["y"]=p.Y};
    private Task Move(int i,Point p)=>Edit(PointArgs(i,p));
    private async Task Save()
    {
        try{await Flush();var choose=new FolderPicker();picker(choose);choose.FileTypeFilter.Add("*");var folder=await choose.PickSingleFolderAsync();if(folder is null)return;string name=System.IO.Path.GetFileName(source).Replace(".model3.json","").Replace(".moc3","")+"-edited-"+DateTime.Now.ToString("yyyyMMdd-HHmmss");var result=await request("live2d_save",new(){["target"]=System.IO.Path.Combine(folder.Path,name)});status.Text=Tr("已导出："+Property(result,"path").GetString());}catch(Exception e){status.Text=Tr(e.Message);}
    }
}
internal static class LiveJsonNumber
{
    internal static double TryNumber(this JsonElement value,double fallback)=>value.ValueKind==JsonValueKind.Number&&value.TryGetDouble(out double n)?n:fallback;
}

public sealed partial class MainWindow
{
    private Window? liveEditorWindow;
    private LiveEditorPane? liveEditor;
    private async void LiveEditor_Click(object sender,RoutedEventArgs e)
    {
        if(package?.Format!="live2d")return;if(liveEditorWindow is {} existing){existing.Activate();return;}Pause();mediaDialogOpen=true;
        if(animation is null&&contents.FirstOrDefault(c=>c.Type=="animation") is {} first)ContentsList.SelectedItem=first;
        var window=new Window{Title=Tr("Live2D 编辑")};liveEditorWindow=window;
        window.AppWindow.SetIcon(System.IO.Path.Combine(AppContext.BaseDirectory,"Application.ico"));
        double scale=Root.XamlRoot?.RasterizationScale??1;
        var work=Microsoft.UI.Windowing.DisplayArea.GetFromWindowId(window.AppWindow.Id,Microsoft.UI.Windowing.DisplayAreaFallback.Primary).WorkArea;
        int width=Math.Min(work.Width-32,(int)(1180*scale)),height=Math.Min(work.Height-32,(int)(820*scale));
        window.AppWindow.MoveAndResize(new Windows.Graphics.RectInt32(work.X+(work.Width-width)/2,work.Y+(work.Height-height)/2,width,height));
        var pane=new LiveEditorPane(package.Path,package.Animations,animation?.Id??0,
            (method,args)=>{if(method=="pick_layer"&&args is not null){args["frame"]=frame;args["renderer"]=RenderMode;}return Request<JsonElement>(method,args);},async()=>{package=await Request<PackageInfo>("info");if(animation is {} a)animation=package.Animations.FirstOrDefault(v=>v.Id==a.Id);await Render(frame);},
            p=>WinRT.Interop.InitializeWithWindow.Initialize(p,WinRT.Interop.WindowNative.GetWindowHandle(window)),TogglePlay,
            id=>{var item=contents.FirstOrDefault(c=>c.Type=="animation"&&c.Index==id);if(item is not null)ContentsList.SelectedItem=item;},value=>{Pause();_=Render(value);});
        liveEditor=pane;window.Content=Tr(pane);pane.Sync(PreviewImage.Source,frame,animation?.Length??1);Root.IsHitTestVisible=false;
        window.Closed+=async(_,_)=>{await pane.Flush();pane.Close();liveEditor=null;liveEditorWindow=null;mediaDialogOpen=false;if(!shuttingDown){Root.IsHitTestVisible=true;Pause();}};
        window.Activate();await pane.Load();
    }
}
