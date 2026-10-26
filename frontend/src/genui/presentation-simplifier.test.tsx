import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { parseUISnapshot, type UISnapshot } from "@/lib/stream-ui";
import { StreamUI } from "@/components/chat/stream-ui";
import { linearChain, presentation, simplifyDocument } from "./presentation-simplifier";
import { documentMarkdown } from "./document-export";

const snapshot = (component: string, extra = {}) => parseUISnapshot({protocol:"suip/0.1",block_id:"test",component,revision:0,state:"completed",props:{title:"分析"},...extra})!;
const chain = {nodes:[{id:"b",label:"分析",description:"拆分数据"},{id:"a",label:"确认",description:"统一口径"}],edges:[{id:"e",source:"a",target:"b"}]};
const document = () => snapshot("document", {blocks:[
  {id:"steps",type:"step_list"},
  {id:"one",parent_id:"steps",type:"rail_item",label:"确认",text:"统一口径"},
  {id:"two",parent_id:"steps",type:"rail_item",label:"分析",text:"拆分数据"},
  {id:"flow",type:"diagram",visual:{props:{title:"链路"},...chain}},
]});

describe("presentation simplifier", () => {
  it("orders a connected chain and retains branches, cycles, disconnected graphs and large graphs as canvas", () => {
    expect(linearChain(snapshot("graph",chain))?.map(n=>n.id)).toEqual(["a","b"]);
    const variants = [
      {...chain, edges:[...chain.edges,{id:"r",source:"b",target:"a"}]},
      {nodes:[...chain.nodes,{id:"c",label:"C"}],edges:[...chain.edges,{id:"f",source:"a",target:"c"}]},
      {nodes:chain.nodes,edges:[]},
      {nodes:Array.from({length:7},(_,i)=>({id:`n${i}`,label:`N${i}`})),edges:[]},
    ];
    for(const graph of variants) expect(presentation(snapshot("graph",graph)).form).toBe("canvas");
  });
  it("shows small tables without controls and ignores hidden stale query/sort preferences", () => {
    localStorage.setItem("learngraph.genui.preferences.v1",JSON.stringify({"guest:local:small-auto:table":{query:"absent",sort:{key:"v",descending:true}}}));
    const data=snapshot("table",{props:{title:"小表格",columns:[{key:"v",label:"值"}]},rows:[{id:"a",label:"A",values:{v:3}},{id:"b",label:"B",values:{v:1}}]});
    render(<StreamUI partId="small-auto" data={data}/>);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button",{name:/值/})).toBeNull();
    expect(screen.getAllByRole("row")[1]).toHaveTextContent("A");
    expect(screen.getByText("B")).toBeInTheDocument();
    expect(presentation({...data,props:{...data.props,interaction:"explore"}}).tableExplore).toBe(true);
    expect(presentation({...data,rows:Array.from({length:9},(_,i)=>({...data.rows[0],id:`r${i}`}))}).tableExplore).toBe(true);
  });
  it("keeps only exact equivalent adjacent representations in render/export without changing durable data", () => {
    const data=document(), original=JSON.stringify(data);
    expect(simplifyDocument(data).map(n=>n.id)).toEqual(["steps","one","two"]);
    expect(documentMarkdown(data)).not.toContain("链路");
    expect(JSON.stringify(data)).toBe(original);
    expect(simplifyDocument({...data,state:"streaming"})).toHaveLength(4);
    for(const delta of [{description:"额外解释"},{source_note:"来源"},{data_kind:"example"},{interaction:"explore"}]) {
      const modified=structuredClone(data);
      modified.blocks![3].visual!.props={title:"链路",...delta};
      expect(simplifyDocument(modified)).toHaveLength(4);
    }
    const different=document();
    (different.blocks![3].visual!.edges as UISnapshot["edges"])[0].label="因果关系";
    expect(simplifyDocument(different)).toHaveLength(4);
    const complementary=document();
    complementary.blocks![1].text="另一种解释";
    expect(simplifyDocument(complementary)).toHaveLength(4);
  });
  it("exposes compact node explanations locally without canvas tools", () => {
    render(<StreamUI partId="compact" data={snapshot("graph",chain)}/>);
    expect(screen.queryByRole("textbox")).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"确认"}));
    expect(screen.getByText("统一口径")).toBeInTheDocument();
    expect(screen.queryByRole("slider")).toBeNull();
  });
  it("separates inline metric units visually while preserving percentage values", () => {
    const data=snapshot("metric",{items:[{id:"count",label:"月活",value:"3,180 人"},{id:"growth",label:"增长",value:"+165%"},{id:"average",label:"月均",value:"约 396 人/月"}]});
    const {container}=render(<StreamUI partId="real-metric-units" data={data}/>);
    const metrics=container.querySelectorAll(".metric-card__value");
    expect(metrics[0].querySelector("span")).toHaveTextContent("3,180");
    expect(metrics[0].querySelector("small")).toHaveTextContent("人");
    expect(metrics[1]).toHaveTextContent("+165%");
    expect(metrics[1].querySelector("small")).toBeNull();
    expect(metrics[2].querySelector("small")).toHaveTextContent("人/月");
    expect(data.items[0].value).toBe("3,180 人");
  });
  it("keeps keyboard tooltip focus when pointer-enter follows an automatic scroll", () => {
    const data=snapshot("chart",{props:{title:"趋势",columns:[{key:"v",label:"人数"}]},rows:[{id:"a",label:"5月",values:{v:1200}},{id:"b",label:"10月",values:{v:3180}}]});
    const {container}=render(<StreamUI partId="keyboard-chart" data={data}/>);
    const first=screen.getByRole("button",{name:"5月: 人数 1,200"});
    const last=screen.getByRole("button",{name:"10月: 人数 3,180"});
    fireEvent.focus(last);
    fireEvent.pointerEnter(first);
    expect(container.querySelector(".stream-ui__tooltip")).toHaveTextContent("10月 · 人数 3,180");
    fireEvent.blur(last);
    expect(container.querySelector(".stream-ui__tooltip")).toHaveTextContent("5月 · 人数 1,200");
  });
  it("keeps static charts readable despite saved hidden-series and range preferences", () => {
    localStorage.setItem("learngraph.genui.preferences.v1",JSON.stringify({"guest:local:static-chart:chart":{mode:"bar",hidden:["v"],limit:3}}));
    const data=snapshot("chart",{props:{title:"静态趋势",interaction:"static",columns:[{key:"v",label:"人数"}]},rows:Array.from({length:6},(_,i)=>({id:`m${i}`,label:`${i+1}月`,values:{v:i+1}}))});
    const {container}=render(<StreamUI partId="static-chart" data={data}/>);
    expect(screen.getByRole("button",{name:"1月: 人数 1"})).toBeInTheDocument();
    expect(container.querySelector("path[stroke='currentColor']")).toBeInTheDocument();
    expect(screen.queryByText("探索图表")).toBeNull();
    expect(JSON.parse(localStorage.getItem("learngraph.genui.preferences.v1")!)["guest:local:static-chart:chart"].hidden).toEqual(["v"]);
  });
  it("defaults chart tools to disclosure and highlights only a valid endpoint without inventing zeroes", () => {
    const data=snapshot("chart",{props:{title:"增长",columns:[{key:"v",label:"用户"}]},rows:[{id:"a",label:"5月",values:{v:1200}},{id:"b",label:"6月",values:{}},{id:"c",label:"7月",values:{v:3180}}]});
    const {container}=render(<StreamUI partId="chart-auto" data={data}/>);
    expect(screen.queryByText("可交互")).toBeNull();
    expect(container.querySelector("details")).not.toHaveAttribute("open");
    expect(screen.getByText("+165%")).toBeInTheDocument();
    expect(container.querySelector(".stream-ui__endpoint")).toHaveTextContent("3,180");
    expect(container.querySelector("path[stroke='currentColor']")?.getAttribute("d")?.match(/M/g)).toHaveLength(2);
    fireEvent.click(screen.getByText("探索图表"));
    expect(screen.getByRole("button",{name:"柱状"})).toBeInTheDocument();
  });
});
