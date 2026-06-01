// ============================================================
// AGENTIC COMMERCE — ARCHITECTURE PRESENTATION
// ============================================================
const pptxgen = require("pptxgenjs");

async function build() {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9"; // 10" × 5.625"
  pres.title = "Agentic Commerce Architecture";
  pres.author = "Agentic Commerce";

  // ── PALETTE (no # prefix) ──────────────────────────────────
  const C = {
    navy:    "1E3A5F",
    navyMd:  "264A72",
    navyLt:  "E8EFF7",
    teal:    "0D9488",
    tealMd:  "14B8A6",
    tealLt:  "CCFBF1",
    amber:   "F59E0B",
    amberLt: "FEF9C3",
    white:   "FFFFFF",
    off:     "F8FAFC",
    dark:    "1E293B",
    mid:     "475569",
    light:   "94A3B8",
    border:  "E2E8F0",
    green:   "16A34A",
    greenLt: "DCFCE7",
    red:     "DC2626",
    redLt:   "FEE2E2",
    purple:  "7C3AED",
    purpleLt:"EDE9FE",
    indigo:  "4338CA",
    indigoLt:"EEF2FF",
  };

  // ── HELPERS ───────────────────────────────────────────────

  // Factory — fresh shadow object each call (pptxgenjs mutates in-place)
  const sh  = () => ({ type:"outer", blur:5, offset:2, angle:135, color:"000000", opacity:0.10 });
  const shS = () => ({ type:"outer", blur:3, offset:1, angle:135, color:"000000", opacity:0.08 });

  function header(s, title, sub) {
    s.addShape(pres.shapes.RECTANGLE, {
      x:0, y:0, w:10, h:0.60,
      fill:{color:C.navy}, line:{color:C.navy}
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x:0, y:0.60, w:10, h:0.055,
      fill:{color:C.teal}, line:{color:C.teal}
    });
    s.addText(title, {
      x:0.4, y:0, w:7.0, h:0.60,
      fontSize:21, bold:true, color:C.white,
      fontFace:"Calibri", margin:0, valign:"middle"
    });
    if (sub) {
      s.addText(sub, {
        x:0, y:0, w:9.55, h:0.60,
        fontSize:10, color:C.tealLt, italic:true,
        fontFace:"Calibri", align:"right", margin:0, valign:"middle"
      });
    }
  }

  function box(s, x, y, w, h, txt, bg, border, txtColor, fontSize=10) {
    s.addShape(pres.shapes.RECTANGLE, {
      x, y, w, h, fill:{color:bg}, line:{color:border, pt:1.5}, shadow:shS()
    });
    s.addText(txt, {
      x, y, w, h, fontSize, color:txtColor,
      fontFace:"Calibri", align:"center", valign:"middle", margin:4
    });
  }

  function accentBox(s, x, y, w, h, txt, accentColor, bgColor, txtColor, fontSize=10) {
    s.addShape(pres.shapes.RECTANGLE, {
      x, y, w, h, fill:{color:bgColor}, line:{color:accentColor, pt:1.5}, shadow:shS()
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x, y, w:0.10, h, fill:{color:accentColor}, line:{color:accentColor}
    });
    s.addText(txt, {
      x:x+0.12, y, w:w-0.12, h, fontSize, color:txtColor,
      fontFace:"Calibri", valign:"middle", margin:4
    });
  }

  function arrow(s, x1, y1, dx, dy, color="94A3B8") {
    s.addShape(pres.shapes.LINE, {
      x:x1, y:y1, w:dx, h:dy,
      line:{color, width:1.5, endArrowType:"open"}
    });
  }

  function tag(s, x, y, label, bg, tc) {
    const w = Math.max(label.length * 0.095 + 0.22, 0.55);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y, w, h:0.27, fill:{color:bg}, line:{color:bg}, rectRadius:0.05
    });
    s.addText(label, {
      x, y, w, h:0.27, fontSize:9, bold:true, color:tc,
      fontFace:"Calibri", align:"center", margin:0, valign:"middle"
    });
    return w + 0.10;
  }

  function footer(s, txt, bg, tc) {
    s.addShape(pres.shapes.RECTANGLE, {
      x:0, y:5.20, w:10, h:0.425, fill:{color:bg}, line:{color:bg}
    });
    s.addText(txt, {
      x:0.35, y:5.20, w:9.3, h:0.425, fontSize:10.5,
      color:tc, fontFace:"Calibri", valign:"middle", margin:0
    });
  }

  // ══════════════════════════════════════════════════════════
  // SLIDE 1 · TITLE
  // ══════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    s.background = { color: C.navy };

    // Left accent stripe
    s.addShape(pres.shapes.RECTANGLE, {
      x:0, y:0, w:0.20, h:5.625, fill:{color:C.teal}, line:{color:C.teal}
    });

    // Decorative circles (top-right)
    s.addShape(pres.shapes.OVAL, {
      x:7.3, y:-1.0, w:3.8, h:3.8, fill:{color:C.navyMd}, line:{color:C.navyMd}
    });
    s.addShape(pres.shapes.OVAL, {
      x:8.2, y:3.4, w:2.4, h:2.4, fill:{color:"192F4D"}, line:{color:"192F4D"}
    });

    // Main title
    s.addText("AGENTIC\nCOMMERCE", {
      x:0.55, y:0.65, w:7.0, h:2.2,
      fontSize:58, bold:true, color:C.white, charSpacing:3,
      fontFace:"Calibri"
    });

    // Teal rule
    s.addShape(pres.shapes.RECTANGLE, {
      x:0.55, y:2.88, w:3.8, h:0.07, fill:{color:C.teal}, line:{color:C.teal}
    });

    // Subtitles
    s.addText("End-to-End Purchasing Agent", {
      x:0.55, y:3.02, w:7.5, h:0.50,
      fontSize:21, color:"A8C4DC", fontFace:"Calibri"
    });
    s.addText("Protocol-First  ·  Multi-Agent  ·  Human-in-the-Loop", {
      x:0.55, y:3.55, w:7.5, h:0.35,
      fontSize:13, color:"5C8AAD", fontFace:"Calibri"
    });

    // Protocol tags
    const tags = [
      {l:"UCP",         bg:C.teal,   tc:C.white},
      {l:"AP2",         bg:C.purple, tc:C.white},
      {l:"Shopify MCP", bg:C.amber,  tc:C.dark},
      {l:"Stripe",      bg:"635BFF", tc:C.white},
      {l:"Claude API",  bg:"CC785C", tc:C.white},
    ];
    let tx = 0.55;
    for (const t of tags) {
      const w = Math.max(t.l.length * 0.105 + 0.30, 0.70);
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x:tx, y:4.08, w, h:0.35, fill:{color:t.bg}, line:{color:t.bg}, rectRadius:0.06
      });
      s.addText(t.l, {
        x:tx, y:4.08, w, h:0.35, fontSize:10, bold:true, color:t.tc,
        fontFace:"Calibri", align:"center", margin:0, valign:"middle"
      });
      tx += w + 0.14;
    }

    // Tagline bottom
    s.addText("Agentic Commerce · Architecture Deck · 2026", {
      x:0.55, y:5.15, w:9, h:0.30,
      fontSize:9.5, color:"3A5F80", fontFace:"Calibri"
    });
  }

  // ══════════════════════════════════════════════════════════
  // SLIDE 2 · VISION
  // ══════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    s.background = { color: C.off };
    header(s, "Vision — What We're Building", "From hours of manual browsing to a 60-second natural-language conversation");

    const cols = [
      {
        x:0.35, title:"THE PROBLEM", accent:C.red, bg:C.redLt,
        items:[
          "Fragmented shopping across dozens of sites",
          "Manual price comparison & merchant research",
          "Repeated checkout flows per merchant",
          "No memory of preferences between sessions",
        ]
      },
      {
        x:3.53, title:"THE SYSTEM", accent:C.teal, bg:C.tealLt,
        items:[
          "Orchestrator + 4 specialist agents",
          "Universal Commerce Protocol (UCP) interface",
          "Discovers, compares & purchases on your behalf",
          "Human confirms before any money is spent",
        ]
      },
      {
        x:6.71, title:"THE RESULT", accent:C.green, bg:C.greenLt,
        items:[
          "Natural language → purchase in 60 seconds",
          "Any UCP-compliant merchant, any payment",
          "Full audit trail of every agent action",
          "New merchants added with zero agent changes",
        ]
      },
    ];

    const CW = 3.0, CH = 4.15;
    for (const col of cols) {
      // Card
      s.addShape(pres.shapes.RECTANGLE, {
        x:col.x, y:0.76, w:CW, h:CH,
        fill:{color:C.white}, line:{color:C.border, pt:1}, shadow:sh()
      });
      // Top accent
      s.addShape(pres.shapes.RECTANGLE, {
        x:col.x, y:0.76, w:CW, h:0.09, fill:{color:col.accent}, line:{color:col.accent}
      });
      // Header bg
      s.addShape(pres.shapes.RECTANGLE, {
        x:col.x, y:0.85, w:CW, h:0.52, fill:{color:col.bg}, line:{color:col.bg}
      });
      s.addText(col.title, {
        x:col.x, y:0.85, w:CW, h:0.52,
        fontSize:12.5, bold:true, color:col.accent,
        fontFace:"Calibri", align:"center", valign:"middle", margin:0
      });
      // Bullet items
      const items = col.items.map((t, i) => ({
        text: t,
        options: { bullet:true, breakLine: i < col.items.length-1 }
      }));
      s.addText(items, {
        x:col.x+0.15, y:1.44, w:CW-0.22, h:CH-1.44+0.76-0.08,
        fontSize:11.5, color:C.dark, fontFace:"Calibri",
        valign:"top", paraSpaceAfter:10
      });
    }

    footer(s,
      "MVP: CLI + Shopify MCP + Stripe test mode — scales automatically to any UCP merchant as protocol adoption grows, zero agent code changes",
      C.navy, C.tealLt
    );
  }

  // ══════════════════════════════════════════════════════════
  // SLIDE 3 · 7-LAYER SYSTEM ARCHITECTURE
  // ══════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    s.background = { color: C.off };
    header(s, "System Architecture — 7 Layers", "Stable contract at L3 · Gateway is the scalability pivot at L4 · Agents only speak UCP vocabulary");

    const LAYERS = [
      { key:"L1", name:"CONVERSATION",   accent:"2196F3", bg:"DBEAFE", tc:"1E40AF",
        left:"User  →  Rich CLI  →  Streaming output",
        right:"HITL Confirmation Gates · Interactive approval before every irreversible action" },
      { key:"L2", name:"AGENT",          accent:"16A34A", bg:"DCFCE7", tc:"166534",
        left:"OrchestratorAgent  (claude-sonnet-4-6)",
        right:"DiscoveryAgent · EvaluationAgent · PurchaseAgent · TrackingAgent  (claude-haiku-4-5)" },
      { key:"L3", name:"TOOL  ⟵  STABLE CONTRACT", accent:"D97706", bg:"FEF9C3", tc:"92400E",
        left:"discovery_tools · evaluation_tools",
        right:"purchase_tools · tracking_tools · shared_tools  (audit_log · validate_mandate)" },
      { key:"L4", name:"GATEWAY",        accent:"DB2777", bg:"FCE7F3", tc:"831843",
        left:"MerchantGateway: try-UCP-first  →  direct fallback",
        right:"PaymentGateway: mandate_id  →  payment token  (raw card never crosses this boundary)" },
      { key:"L5", name:"PROTOCOL",       accent:"7C3AED", bg:"EDE9FE", tc:"5B21B6",
        left:"UCPRestClient · UCPMCPClient · RequestSigner (RFC 9421)",
        right:"AP2MandateEngine (local, HMAC-signed) · StripeAdapter · CapabilityNegotiator" },
      { key:"L6", name:"INTEGRATION",    accent:"0D9488", bg:"CCFBF1", tc:"134E4A",
        left:"ShopifyMCPAdapter (MVP) · GenericUCPAdapter (any UCP merchant)",
        right:"UCPProfileDiscovery: /.well-known/ucp  →  stub fallback · 60 s client cache" },
      { key:"L7", name:"DATA",           accent:"B45309", bg:"FEF3C7", tc:"78350F",
        left:"TinyDB (MVP)  →  PostgreSQL + Redis (target)",
        right:"mandates · orders · audit_log · spend_records · profile_cache" },
    ];

    const RH = 0.585, GAP = 0.012;
    const Y0 = 0.77;
    const LX = 0.32, LW = 9.36;
    const ACW = 0.13, KW = 0.42, NW = 2.05, COL1W = 3.20, COL2W = 3.50;

    for (let i = 0; i < LAYERS.length; i++) {
      const L = LAYERS[i];
      const y = Y0 + i * (RH + GAP);

      // Row bg
      s.addShape(pres.shapes.RECTANGLE, {
        x:LX, y, w:LW, h:RH, fill:{color:L.bg}, line:{color:L.accent, pt:0.75}
      });
      // Accent bar
      s.addShape(pres.shapes.RECTANGLE, {
        x:LX, y, w:ACW, h:RH, fill:{color:L.accent}, line:{color:L.accent}
      });
      // Key (L1…)
      s.addText(L.key, {
        x:LX+ACW, y, w:KW, h:RH,
        fontSize:12.5, bold:true, color:L.accent,
        fontFace:"Calibri", align:"center", valign:"middle", margin:0
      });
      // Name
      s.addText(L.name, {
        x:LX+ACW+KW, y, w:NW, h:RH,
        fontSize:9, bold:true, color:L.tc,
        fontFace:"Calibri", valign:"middle", margin:5
      });
      // Dividers
      const d1x = LX+ACW+KW+NW;
      s.addShape(pres.shapes.LINE, { x:d1x, y:y+0.1, w:0, h:RH-0.2, line:{color:L.accent, width:0.75} });
      const d2x = d1x + 0.08 + COL1W;
      s.addShape(pres.shapes.LINE, { x:d2x, y:y+0.1, w:0, h:RH-0.2, line:{color:L.accent, width:0.75} });
      // Left content
      s.addText(L.left, {
        x:d1x+0.08, y, w:COL1W, h:RH, fontSize:9, color:C.dark,
        fontFace:"Calibri", valign:"middle", margin:4
      });
      // Right content
      s.addText(L.right, {
        x:d2x+0.08, y, w:COL2W, h:RH, fontSize:9, color:C.mid,
        fontFace:"Calibri", valign:"middle", margin:4
      });
    }
  }

  // ══════════════════════════════════════════════════════════
  // SLIDE 4 · AGENT TEAM
  // ══════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    s.background = { color: C.off };
    header(s, "Multi-Agent Orchestration", "All coordination via Orchestrator tool calls · No direct agent-to-agent communication · Subagents return Pydantic objects");

    // Orchestrator
    s.addShape(pres.shapes.RECTANGLE, {
      x:1.0, y:0.78, w:8.0, h:0.98,
      fill:{color:C.navy}, line:{color:C.teal, pt:2}, shadow:sh()
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x:1.0, y:0.78, w:0.13, h:0.98, fill:{color:C.teal}, line:{color:C.teal}
    });
    s.addText("ORCHESTRATOR AGENT", {
      x:1.18, y:0.78, w:7.7, h:0.48,
      fontSize:14, bold:true, color:C.white, fontFace:"Calibri", valign:"middle", margin:0
    });
    s.addText("claude-sonnet-4-6  ·  streaming  ·  HITL safety gates  ·  subagent coordination  ·  mandate verification  ·  audit logging", {
      x:1.18, y:1.22, w:7.7, h:0.48,
      fontSize:10, color:C.tealLt, fontFace:"Calibri", valign:"middle", margin:0
    });

    // Subagents
    const agents = [
      { title:"DISCOVERY\nAGENT", accent:"2196F3", bg:"DBEAFE",
        model:"claude-haiku-4-5",
        tools:["search_products()", "get_product_details()", "check_stock()"],
        note:"Sets confidence_score\nper result. Returns\nin-stock items only." },
      { title:"EVALUATION\nAGENT", accent:"16A34A", bg:"DCFCE7",
        model:"claude-haiku-4-5",
        tools:["rank_products()", "fetch_reviews()", "compare_prices()"],
        note:"Weighted scoring.\nFlags LOW_CONFIDENCE\nif top pick < 0.8." },
      { title:"PURCHASE\nAGENT", accent:C.purple, bg:C.purpleLt,
        model:"claude-haiku-4-5",
        tools:["validate_mandate()", "create_checkout()", "complete_order()"],
        note:"Double-validates mandate.\nOnly receives mandate_id.\nNever sees card data." },
      { title:"TRACKING\nAGENT", accent:C.teal, bg:C.tealLt,
        model:"claude-haiku-4-5",
        tools:["get_order_status()", "initiate_return()", "check_refund()"],
        note:"Polls order status.\nNo autonomous returns.\nRequires user confirm." },
    ];

    const AW = 2.15, AH = 3.05;
    const AY = 2.05;
    const GAP = 0.10;
    const TOTAL = agents.length * AW + (agents.length-1) * GAP;
    const AX0 = (10 - TOTAL) / 2;

    for (let i = 0; i < agents.length; i++) {
      const A = agents[i];
      const ax = AX0 + i * (AW + GAP);

      // Arrow from orchestrator
      const agentCX = ax + AW/2;
      s.addShape(pres.shapes.LINE, {
        x:agentCX, y:1.76, w:0, h:0.26,
        line:{color:A.accent, width:1.5, dashType:"dash", endArrowType:"open"}
      });

      // Card
      s.addShape(pres.shapes.RECTANGLE, {
        x:ax, y:AY, w:AW, h:AH,
        fill:{color:C.white}, line:{color:A.accent, pt:1.5}, shadow:sh()
      });
      // Top bar
      s.addShape(pres.shapes.RECTANGLE, {
        x:ax, y:AY, w:AW, h:0.09, fill:{color:A.accent}, line:{color:A.accent}
      });
      // Title bg
      s.addShape(pres.shapes.RECTANGLE, {
        x:ax, y:AY+0.09, w:AW, h:0.60, fill:{color:A.bg}, line:{color:A.bg}
      });
      s.addText(A.title, {
        x:ax, y:AY+0.09, w:AW, h:0.60,
        fontSize:11, bold:true, color:A.accent,
        fontFace:"Calibri", align:"center", valign:"middle", margin:0
      });
      // Model badge
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x:ax+0.14, y:AY+0.79, w:AW-0.28, h:0.26,
        fill:{color:C.navy}, line:{color:C.navy}, rectRadius:0.04
      });
      s.addText(A.model, {
        x:ax+0.14, y:AY+0.79, w:AW-0.28, h:0.26,
        fontSize:8.5, bold:true, color:C.tealLt,
        fontFace:"Calibri", align:"center", margin:0, valign:"middle"
      });
      // Tools label
      s.addText("TOOLS", {
        x:ax+0.12, y:AY+1.12, w:AW-0.24, h:0.22,
        fontSize:8, bold:true, color:A.accent, fontFace:"Calibri", margin:0
      });
      // Tool list
      const toolItems = A.tools.map((t, ti) => ({
        text:t, options:{bullet:true, breakLine:ti < A.tools.length-1}
      }));
      s.addText(toolItems, {
        x:ax+0.12, y:AY+1.32, w:AW-0.22, h:0.85,
        fontSize:9.5, color:C.dark, fontFace:"Calibri", valign:"top", paraSpaceAfter:2
      });
      // Note bg
      s.addShape(pres.shapes.RECTANGLE, {
        x:ax, y:AY+AH-0.52, w:AW, h:0.52,
        fill:{color:A.bg}, line:{color:A.bg}
      });
      s.addShape(pres.shapes.RECTANGLE, {
        x:ax, y:AY+AH-0.52, w:AW, h:0.04, fill:{color:A.accent}, line:{color:A.accent}
      });
      s.addText(A.note, {
        x:ax+0.08, y:AY+AH-0.48, w:AW-0.16, h:0.48,
        fontSize:8.5, color:A.accent, italic:true,
        fontFace:"Calibri", align:"center", valign:"middle", margin:0
      });
    }
  }

  // ══════════════════════════════════════════════════════════
  // SLIDE 5 · PURCHASE FLOW
  // ══════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    s.background = { color: C.off };
    header(s, "End-to-End Purchase Flow", "Discovery → Evaluation → HITL Confirmation Gate → Purchase Execution → Order Confirmed");

    // Phase bands (row 1: steps 1-5, row 2: steps 6-10)
    const phases = [
      { label:"DISCOVERY",  color:C.teal,   steps:[1,2,3],   row:0, startIdx:0 },
      { label:"EVALUATION", color:C.green,  steps:[4,5],     row:0, startIdx:3 },
      { label:"HITL GATE",  color:C.amber,  steps:[6],       row:1, startIdx:0 },
      { label:"PURCHASE",   color:C.purple, steps:[7,8,9,10],row:1, startIdx:1 },
    ];

    const steps = [
      // Row 0 — steps 1-5
      { n:1, actor:"User",          desc:"Express intent in natural language",              color:C.navy,   row:0, col:0 },
      { n:2, actor:"Discovery Agt", desc:"Fan-out search across merchants via MCP / UCP",  color:C.teal,   row:0, col:1 },
      { n:3, actor:"Gateway",       desc:"Fetch /.well-known/ucp, negotiate capabilities", color:C.teal,   row:0, col:2 },
      { n:4, actor:"Eval Agent",    desc:"Weighted scoring: preference, price, trust, shipping", color:C.green, row:0, col:3 },
      { n:5, actor:"Orchestrator",  desc:"Present ranked options to user (Rich cards)",    color:C.indigo, row:0, col:4 },
      // Row 1 — steps 6-10
      { n:6,  actor:"User → Orch",  desc:"⚠ CONFIRM gate · Risk-tiered human approval",   color:C.amber,  row:1, col:0 },
      { n:7,  actor:"Purchase Agt", desc:"Validate AgentMandate (HMAC + cap check)",       color:C.purple, row:1, col:1 },
      { n:8,  actor:"Gateway",      desc:"POST /checkout-sessions + PUT with items/buyer", color:C.teal,   row:1, col:2 },
      { n:9,  actor:"Stripe",       desc:"Tokenise payment · Trust Triangle · No raw card",color:C.purple, row:1, col:3 },
      { n:10, actor:"Merchant",     desc:"POST /complete · Order confirmed · Mandate spend recorded", color:C.green, row:1, col:4 },
    ];

    const SW = 1.68, SH = 1.50, SGAP = 0.14;
    const ROW_Y = [1.05, 2.82];  // push below header (0.655") + leave room for phase label (0.28")
    const X0 = 0.45;

    // Phase banners
    const phaseLayout = [
      { phase:"DISCOVERY",  color:C.teal,   x:X0,                                          cols:3 },
      { phase:"EVALUATION", color:C.green,  x:X0 + 3*(SW+SGAP),                           cols:2 },
      { phase:"HITL GATE",  color:C.amber,  x:X0,                                          cols:1 },
      { phase:"PURCHASE",   color:C.purple, x:X0 + 1*(SW+SGAP),                           cols:4 },
    ];
    const bannerRows = [[0,1],[2,3]];
    for (let ri = 0; ri < bannerRows.length; ri++) {
      const idxs = bannerRows[ri];
      for (const pi of idxs) {
        const ph = phaseLayout[pi];
        const bw = ph.cols * SW + (ph.cols-1) * SGAP;
        const by = ROW_Y[ri] - 0.28;
        s.addShape(pres.shapes.RECTANGLE, {
          x:ph.x, y:by, w:bw, h:0.26, fill:{color:ph.color}, line:{color:ph.color}
        });
        s.addText(ph.phase, {
          x:ph.x, y:by, w:bw, h:0.26,
          fontSize:8.5, bold:true, color:C.white,
          fontFace:"Calibri", align:"center", margin:0, valign:"middle"
        });
      }
    }

    // Step boxes
    for (const ST of steps) {
      const sx = X0 + ST.col * (SW + SGAP);
      const sy = ROW_Y[ST.row];

      // Card
      s.addShape(pres.shapes.RECTANGLE, {
        x:sx, y:sy, w:SW, h:SH,
        fill:{color:C.white}, line:{color:ST.color, pt:1.5}, shadow:shS()
      });
      s.addShape(pres.shapes.RECTANGLE, {
        x:sx, y:sy, w:SW, h:0.08, fill:{color:ST.color}, line:{color:ST.color}
      });
      // Step number circle bg
      s.addShape(pres.shapes.OVAL, {
        x:sx+0.08, y:sy+0.14, w:0.35, h:0.35,
        fill:{color:ST.color}, line:{color:ST.color}
      });
      s.addText(String(ST.n), {
        x:sx+0.08, y:sy+0.14, w:0.35, h:0.35,
        fontSize:11, bold:true, color:C.white,
        fontFace:"Calibri", align:"center", valign:"middle", margin:0
      });
      // Actor
      s.addText(ST.actor, {
        x:sx+0.48, y:sy+0.12, w:SW-0.55, h:0.35,
        fontSize:9, bold:true, color:ST.color,
        fontFace:"Calibri", valign:"middle", margin:0
      });
      // Divider line
      s.addShape(pres.shapes.LINE, {
        x:sx+0.08, y:sy+0.54, w:SW-0.16, h:0,
        line:{color:C.border, width:0.75}
      });
      // Description
      s.addText(ST.desc, {
        x:sx+0.08, y:sy+0.60, w:SW-0.16, h:SH-0.65,
        fontSize:9.5, color:C.dark, fontFace:"Calibri",
        valign:"top", margin:0
      });

      // Arrow to next step in same row
      if (ST.col < 4) {
        s.addShape(pres.shapes.LINE, {
          x:sx+SW, y:sy+SH/2, w:SGAP, h:0,
          line:{color:C.light, width:1.2, endArrowType:"open"}
        });
      }
    }

    // Arrow from row 0 end to row 1 start (step 5 → step 6)
    const row0EndX = X0 + 4*(SW+SGAP) + SW;
    const row0MidY = ROW_Y[0] + SH/2;
    const row1StartX = X0;
    const row1MidY = ROW_Y[1] + SH/2;
    s.addShape(pres.shapes.LINE, {
      x:row0EndX, y:row0MidY, w:0.35, h:0,
      line:{color:C.mid, width:1.5}
    });
    const cornerX = row0EndX + 0.35;
    s.addShape(pres.shapes.LINE, {
      x:cornerX, y:row0MidY, w:0, h:row1MidY - row0MidY,
      line:{color:C.mid, width:1.5}
    });
    s.addShape(pres.shapes.LINE, {
      x:row1StartX, y:row1MidY, w:cornerX - row1StartX, h:0,
      line:{color:C.mid, width:1.5, endArrowType:"open"}
    });
  }

  // ══════════════════════════════════════════════════════════
  // SLIDE 6 · MERCHANT GATEWAY ROUTING
  // ══════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    s.background = { color: C.off };
    header(s, "MerchantGateway — The Scalability Engine", "Every merchant call routes through here · Agents never know which path ran · New merchants = zero agent changes");

    // ── Main flow (left column) — compact so all fits above footer ──
    const CX = 2.7;
    const BW = 2.6;
    const BH = 0.42;   // reduced from 0.52
    const ARR = 0.14;  // reduced arrow gap
    const BX = CX - BW/2;

    let y = 0.80;

    // START terminator
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x:BX, y, w:BW, h:BH, fill:{color:C.navy}, line:{color:C.teal, pt:2}, rectRadius:0.20
    });
    s.addText("TOOL CALL  ·  search_products(domain)", {
      x:BX, y, w:BW, h:BH, fontSize:10, bold:true, color:C.white,
      fontFace:"Calibri", align:"center", valign:"middle", margin:0
    });
    y += BH; arrow(s, CX, y, 0, ARR); y += ARR;

    // Decision 1 — cache check
    s.addShape(pres.shapes.RECTANGLE, {
      x:BX, y, w:BW, h:BH, fill:{color:C.amberLt}, line:{color:C.amber, pt:2}, shadow:shS()
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x:BX, y, w:0.10, h:BH, fill:{color:C.amber}, line:{color:C.amber}
    });
    s.addText("Client cached?  (60 s TTL)", {
      x:BX+0.12, y, w:BW-0.12, h:BH, fontSize:10.5, bold:true, color:"92400E",
      fontFace:"Calibri", align:"center", valign:"middle", margin:0
    });
    const D1Y = y; y += BH;

    // YES from D1 — inline callout box (avoids complex multi-segment path)
    s.addShape(pres.shapes.LINE, {
      x:BX+BW, y:D1Y+BH/2, w:0.90, h:0,
      line:{color:C.green, width:1.5, endArrowType:"open"}
    });
    s.addText("YES", {
      x:BX+BW+0.02, y:D1Y+BH/2-0.22, w:0.45, h:0.20,
      fontSize:8.5, bold:true, color:C.green, italic:true, fontFace:"Calibri"
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x:BX+BW+0.93, y:D1Y, w:1.70, h:BH,
      fill:{color:C.greenLt}, line:{color:C.green, pt:1}
    });
    s.addText("Use cached client\n→ jump to Execute", {
      x:BX+BW+0.95, y:D1Y, w:1.66, h:BH,
      fontSize:8.5, color:C.green, fontFace:"Calibri",
      align:"center", valign:"middle", margin:0
    });

    arrow(s, CX, y, 0, ARR, C.mid);
    s.addText("No", { x:CX+0.06, y:y, w:0.4, h:ARR, fontSize:9, color:C.red, italic:true, fontFace:"Calibri" });
    y += ARR;

    // UCPProfileDiscovery
    accentBox(s, BX, y, BW, BH, "UCPProfileDiscovery\n.try_discover(domain)", C.teal, C.tealLt, C.dark, 10);
    y += BH; arrow(s, CX, y, 0, ARR); y += ARR;

    // Decision 2 — profile found?
    s.addShape(pres.shapes.RECTANGLE, {
      x:BX, y, w:BW, h:BH, fill:{color:C.amberLt}, line:{color:C.amber, pt:2}, shadow:shS()
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x:BX, y, w:0.10, h:BH, fill:{color:C.amber}, line:{color:C.amber}
    });
    s.addText("/.well-known/ucp  found & valid?", {
      x:BX+0.12, y, w:BW-0.12, h:BH, fontSize:10.5, bold:true, color:"92400E",
      fontFace:"Calibri", align:"center", valign:"middle", margin:0
    });
    const D2Y = y; y += BH;

    arrow(s, CX, y, 0, ARR);
    s.addText("Yes", { x:CX+0.06, y:y, w:0.4, h:ARR, fontSize:9, color:C.green, italic:true, fontFace:"Calibri" });
    y += ARR;

    // Negotiate
    accentBox(s, BX, y, BW, BH, "Parse UCPProfile\nNegotiate capabilities & transport", C.purple, C.purpleLt, C.dark, 10);
    y += BH; arrow(s, CX, y, 0, ARR); y += ARR;

    // Cache + execute
    accentBox(s, BX, y, BW, BH, "Cache client (60 s TTL)  →  Execute", C.green, C.greenLt, C.dark, 10);
    y += BH; arrow(s, CX, y, 0, ARR, C.green); y += ARR;

    // END terminator
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x:BX, y, w:BW, h:BH, fill:{color:C.green}, line:{color:C.green}, rectRadius:0.20
    });
    s.addText("ProductResult[]  ·  CheckoutSession  ·  etc.", {
      x:BX, y, w:BW, h:BH, fontSize:10, bold:true, color:C.white,
      fontFace:"Calibri", align:"center", valign:"middle", margin:0
    });

    // ── RIGHT COLUMN  (No path from D2 → transport options + fallback) ──
    const NO_RX = 5.55;  // start x right of main flow
    const TW = 3.45;     // NO_RX + TW = 9.0 — fits within slide
    const TH = 0.42;     // matches BH

    // "No" branch from D2 — go right then down
    s.addShape(pres.shapes.LINE, {
      x:BX+BW, y:D2Y+BH/2, w:NO_RX-(BX+BW), h:0,
      line:{color:C.mid, width:1.5}
    });
    s.addText("No", { x:BX+BW+0.05, y:D2Y+BH/2-0.20, w:0.4, h:0.20, fontSize:9, color:C.red, italic:true, fontFace:"Calibri" });
    s.addShape(pres.shapes.LINE, {
      x:NO_RX, y:D2Y+BH/2, w:0, h:0.28,
      line:{color:C.mid, width:1.5, endArrowType:"open"}
    });

    // UCP-native transport options
    const TY0 = D2Y + BH/2 + 0.28;
    s.addText("UCP-native transport:", {
      x:NO_RX, y:TY0-0.24, w:TW, h:0.22,
      fontSize:9, bold:true, color:C.mid, fontFace:"Calibri"
    });
    const transports = [
      { label:"REST  ·  UCPRestClient + RFC 9421 signing",  accent:C.teal,  bg:C.tealLt,  tag:"MVP" },
      { label:"MCP   ·  UCPMCPClient  JSON-RPC 2.0",        accent:C.green, bg:C.greenLt, tag:"MVP" },
      { label:"A2A   ·  UCPA2AClient  Agent-to-Agent",      accent:C.light, bg:"F1F5F9",  tag:"Phase 2" },
    ];
    for (let i = 0; i < transports.length; i++) {
      const T = transports[i];
      const ty = TY0 + i*(TH+0.08);
      s.addShape(pres.shapes.RECTANGLE, {
        x:NO_RX, y:ty, w:TW, h:TH, fill:{color:T.bg}, line:{color:T.accent, pt:1.25}, shadow:shS()
      });
      s.addShape(pres.shapes.RECTANGLE, {
        x:NO_RX, y:ty, w:0.10, h:TH, fill:{color:T.accent}, line:{color:T.accent}
      });
      s.addText(T.label, {
        x:NO_RX+0.14, y:ty, w:TW-0.72, h:TH, fontSize:9.5, color:C.dark,
        fontFace:"Calibri", valign:"middle", margin:0
      });
      const tagBg = T.tag === "MVP" ? C.teal : C.light;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x:NO_RX+TW-0.70, y:ty+0.08, w:0.66, h:0.25,
        fill:{color:tagBg}, line:{color:tagBg}, rectRadius:0.04
      });
      s.addText(T.tag, {
        x:NO_RX+TW-0.70, y:ty+0.08, w:0.66, h:0.25,
        fontSize:8, bold:true, color:C.white,
        fontFace:"Calibri", align:"center", margin:0, valign:"middle"
      });
    }

    // Direct adapter (No UCP profile fallback) — must end before 5.15" (footer at 5.20")
    const DA_Y = TY0 + 3*(TH+0.08) + 0.10;
    s.addText("No UCP profile — direct fallback:", {
      x:NO_RX, y:DA_Y-0.23, w:TW, h:0.21,
      fontSize:9, bold:true, color:C.mid, fontFace:"Calibri"
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x:NO_RX, y:DA_Y, w:TW, h:TH, fill:{color:C.amberLt}, line:{color:C.amber, pt:1.5}, shadow:shS()
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x:NO_RX, y:DA_Y, w:0.10, h:TH, fill:{color:C.amber}, line:{color:C.amber}
    });
    s.addText("ShopifyMCPAdapter  (or custom adapter)", {
      x:NO_RX+0.14, y:DA_Y, w:TW-0.16, h:TH, fontSize:9.5, color:"92400E",
      fontFace:"Calibri", valign:"middle", margin:0
    });

    footer(s,
      "KEY INSIGHT: UCP-native merchant → 1 line of config. Non-UCP merchant → 1 adapter class. In both cases: zero agent code changes.",
      C.navy, C.tealLt
    );
  }

  // ══════════════════════════════════════════════════════════
  // SLIDE 7 · SAFETY — DEFENCE IN DEPTH
  // ══════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    s.background = { color: C.off };
    header(s, "Safety — Defence in Depth", "Five independent protection layers · A purchase must pass ALL five · Any failure blocks and informs the user");

    const layers = [
      { n:"1", title:"MANDATE BOUNDS",   accent:"1565C0", bg:"DBEAFE",
        desc:"AgentMandate: HMAC-SHA256 signed · per-transaction cap · daily cap · monthly cap · instant revocation at any time",
        fail:"Revoked / expired\nor cap exceeded" },
      { n:"2", title:"HITL GATE",        accent:"15803D", bg:"DCFCE7",
        desc:"< $30 trusted vendor → soft confirm (Enter)   ·   > $100 → type CONFIRM   ·   > $500 → CONFIRM + full order summary panel",
        fail:"User cancels\nor no response" },
      { n:"3", title:"GUARDRAILS",       accent:"B45309", bg:"FEF9C3",
        desc:"VendorGate: allowlist / blocklist   ·   SpendingLimiter: DB-backed cap check   ·   ConfidenceChecker: agent score < 0.8 → escalate",
        fail:"Vendor blocklisted\nor low confidence" },
      { n:"4", title:"AUDIT LOG",        accent:"9D174D", bg:"FCE7F3",
        desc:"Every agent action logged BEFORE execution, not after · Never deleted · Includes mandate_id · If DB write fails → abort transaction",
        fail:"DB write fails\n→ abort" },
      { n:"5", title:"PAYMENT ISOLATION",accent:"5B21B6", bg:"EDE9FE",
        desc:"payment_method_id never appears in agent context window · PaymentGateway only · Stripe tokenises · PCI-DSS scope = Stripe's problem",
        fail:"Stripe error → halt\nno auto-retry" },
    ];

    const LH = 0.70, GAP = 0.07;
    const LY0 = 0.77;
    const LX = 0.35, MAIN_W = 7.85;
    const FAIL_W = 1.38;
    const NUM_W = 0.52, TITLE_W = 1.82, DESC_W = MAIN_W - NUM_W - TITLE_W;

    for (let i = 0; i < layers.length; i++) {
      const L = layers[i];
      const y = LY0 + i*(LH+GAP);

      // Row bg
      s.addShape(pres.shapes.RECTANGLE, {
        x:LX, y, w:MAIN_W, h:LH, fill:{color:L.bg}, line:{color:L.accent, pt:1}
      });
      // Number circle
      s.addShape(pres.shapes.OVAL, {
        x:LX+0.06, y:y+0.09, w:NUM_W-0.12, h:LH-0.18,
        fill:{color:L.accent}, line:{color:L.accent}
      });
      s.addText(L.n, {
        x:LX+0.06, y:y+0.09, w:NUM_W-0.12, h:LH-0.18,
        fontSize:18, bold:true, color:C.white,
        fontFace:"Calibri", align:"center", valign:"middle", margin:0
      });
      // Title bar
      s.addShape(pres.shapes.RECTANGLE, {
        x:LX+NUM_W, y, w:TITLE_W, h:LH, fill:{color:L.accent}, line:{color:L.accent}
      });
      s.addText(L.title, {
        x:LX+NUM_W, y, w:TITLE_W, h:LH,
        fontSize:10.5, bold:true, color:C.white,
        fontFace:"Calibri", align:"center", valign:"middle", margin:4
      });
      // Description
      s.addText(L.desc, {
        x:LX+NUM_W+TITLE_W+0.10, y, w:DESC_W-0.10, h:LH,
        fontSize:9.5, color:C.dark, fontFace:"Calibri", valign:"middle", margin:4
      });
      // Arrow down (except last)
      if (i < layers.length-1) {
        s.addShape(pres.shapes.LINE, {
          x:LX+MAIN_W/2, y:y+LH, w:0, h:GAP,
          line:{color:C.mid, width:1.5, endArrowType:"open"}
        });
      }
      // Fail box (right)
      const FAIL_X = LX + MAIN_W + 0.12;
      s.addShape(pres.shapes.RECTANGLE, {
        x:FAIL_X, y:y+0.08, w:FAIL_W, h:LH-0.16,
        fill:{color:C.redLt}, line:{color:C.red, pt:0.75}
      });
      s.addText("BLOCKED\n" + L.fail, {
        x:FAIL_X, y:y+0.08, w:FAIL_W, h:LH-0.16,
        fontSize:8, color:C.red, fontFace:"Calibri",
        align:"center", valign:"middle", margin:2
      });
      // Dashed arrow to fail
      s.addShape(pres.shapes.LINE, {
        x:LX+MAIN_W, y:y+LH/2, w:0.12, h:0,
        line:{color:C.red, width:1, dashType:"dash"}
      });
    }

    // Execute
    const EXEC_Y = LY0 + layers.length*(LH+GAP);
    s.addShape(pres.shapes.LINE, {
      x:LX+MAIN_W/2, y:EXEC_Y, w:0, h:0.18,
      line:{color:C.green, width:2, endArrowType:"open"}
    });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x:LX+MAIN_W/2-1.15, y:EXEC_Y+0.18, w:2.30, h:0.38,
      fill:{color:C.green}, line:{color:C.green}, rectRadius:0.18
    });
    s.addText("PURCHASE EXECUTED", {
      x:LX+MAIN_W/2-1.15, y:EXEC_Y+0.18, w:2.30, h:0.38,
      fontSize:11, bold:true, color:C.white,
      fontFace:"Calibri", align:"center", valign:"middle", margin:0
    });
  }

  // ══════════════════════════════════════════════════════════
  // SLIDE 8 · DATA MODEL
  // ══════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    s.background = { color: C.off };
    header(s, "Core Data Model — UCP Vocabulary", "UCP field names are the stable contract · payment_method_id is structurally isolated from agent context");

    function entity(slide, x, y, w, name, accent, bg, fields) {
      const FH = 0.21;
      const totalH = 0.32 + fields.length * FH + 0.04;
      slide.addShape(pres.shapes.RECTANGLE, {
        x, y, w, h:totalH, fill:{color:C.white}, line:{color:accent, pt:1.5}, shadow:sh()
      });
      slide.addShape(pres.shapes.RECTANGLE, {
        x, y, w, h:0.32, fill:{color:accent}, line:{color:accent}
      });
      slide.addText(name, {
        x, y, w, h:0.32, fontSize:10.5, bold:true, color:C.white,
        fontFace:"Calibri", align:"center", valign:"middle", margin:0
      });
      for (let fi = 0; fi < fields.length; fi++) {
        const fy = y + 0.32 + fi * FH;
        if (fi % 2 === 0) {
          slide.addShape(pres.shapes.RECTANGLE, {
            x, y:fy, w, h:FH, fill:{color:bg}, line:{color:bg}
          });
        }
        slide.addText(fields[fi], {
          x:x+0.10, y:fy, w:w-0.12, h:FH,
          fontSize:8.5, color:C.dark, fontFace:"Calibri", valign:"middle", margin:0
        });
      }
      return totalH;
    }

    const EW = 2.25;

    // Col 1
    entity(s, 0.28, 0.80, EW, "UserProfile", C.navy, C.navyLt,
      ["user_id (PK)", "name · email", "addresses[ ]", "vendor_allowlist[ ]", "vendor_blocklist[ ]"]);

    entity(s, 0.28, 2.78, EW, "AgentMandate", C.purple, C.purpleLt,
      ["mandate_id (PK)", "user_id (FK)", "max_amount · daily_cap · monthly_cap",
       "allowed_categories[ ]  ·  allowed_vendors[ ]",
       "expiry · revoked", "digital_signature (HMAC-SHA256)"]);

    // Col 2
    entity(s, 3.05, 0.80, EW, "UCPProfile", C.teal, C.tealLt,
      ["merchant_domain (PK)", "capabilities[ ]  (namespaces)", "services[ ]  (transport type)",
       "payment_handlers[ ]", "signing_keys  (JWK)", "cached_at"]);

    entity(s, 3.05, 2.78, EW, "CheckoutSession", "0D9488", "E6FAF8",
      ["session_id (PK)", "merchant_domain (FK)",
       "line_items[ ]  ·  currency", "subtotal · discount · tax · total", "status  (open / completed)"]);

    // Col 3
    entity(s, 5.82, 0.80, EW, "PurchaseOrder", C.green, C.greenLt,
      ["order_id (PK)", "session_id (FK)", "mandate_id (FK)  ←  never card",
       "items[ ]  ·  total  ·  status", "tracking_number", "created_at"]);

    entity(s, 5.82, 2.78, EW, "AuditEntry", "9D174D", "FCE7F3",
      ["entry_id (PK)", "mandate_id (FK)", "agent  ·  tool  ·  action", "timestamp"]);

    // Payment Isolated — Col 4 (red dashed)
    const PIX = 8.30, PIY = 0.80, PIW = 1.42;
    s.addShape(pres.shapes.RECTANGLE, {
      x:PIX, y:PIY, w:PIW, h:1.62,
      fill:{color:"FFF0F0"}, line:{color:C.red, pt:2, dashType:"dash"}
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x:PIX, y:PIY, w:PIW, h:0.32, fill:{color:C.red}, line:{color:C.red}
    });
    s.addText("PaymentCredential", {
      x:PIX, y:PIY, w:PIW, h:0.32,
      fontSize:8.5, bold:true, color:C.white,
      fontFace:"Calibri", align:"center", valign:"middle", margin:0
    });
    s.addText("payment_method_id\nlast_four · brand", {
      x:PIX+0.08, y:PIY+0.36, w:PIW-0.12, h:0.60,
      fontSize:8.5, color:C.red, fontFace:"Calibri", valign:"middle"
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x:PIX, y:PIY+1.0, w:PIW, h:0.62, fill:{color:C.redLt}, line:{color:C.redLt}
    });
    s.addText("🔒 ISOLATED\nNever visible\nto agents", {
      x:PIX, y:PIY+1.0, w:PIW, h:0.62,
      fontSize:8.5, bold:true, color:C.red,
      fontFace:"Calibri", align:"center", valign:"middle"
    });

    // Relationship lines (dashed arrows)
    const rels = [
      { x1:0.28+EW/2, y1:0.80+0.32+5*0.21, x2:0.28+EW/2, y2:2.78, label:"grants" },
      { x1:3.05+EW/2, y1:0.80+0.32+5*0.21, x2:3.05+EW/2, y2:2.78, label:"serves" },
      { x1:0.28+EW,   y1:3.4,               x2:3.05,      y2:3.4,  label:"authorises" },
      { x1:3.05+EW,   y1:3.4,               x2:5.82,      y2:3.4,  label:"becomes" },
      { x1:5.82+EW/2, y1:0.80+0.32+5*0.21, x2:5.82+EW/2, y2:2.78, label:"logs" },
    ];
    for (const R of rels) {
      const dx = R.x2 - R.x1, dy = R.y2 - R.y1;
      s.addShape(pres.shapes.LINE, {
        x:R.x1, y:R.y1, w:dx, h:dy,
        line:{color:C.light, width:1, dashType:"sysDash", endArrowType:"open"}
      });
      const lx = R.x1+dx/2, ly = R.y1+dy/2;
      s.addShape(pres.shapes.RECTANGLE, {
        x:lx-0.32, y:ly-0.12, w:0.64, h:0.22,
        fill:{color:C.off}, line:{color:C.off}
      });
      s.addText(R.label, {
        x:lx-0.32, y:ly-0.12, w:0.64, h:0.22,
        fontSize:7.5, color:C.mid, italic:true,
        fontFace:"Calibri", align:"center", margin:0
      });
    }
  }

  // ══════════════════════════════════════════════════════════
  // SLIDE 9 · PROGRESSIVE ENHANCEMENT
  // ══════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    s.background = { color: C.off };
    header(s, "Progressive Enhancement — No Rewrites Required", "MVP works today with Shopify MCP + Stripe · Each capability activates automatically as UCP adoption grows · Agents never change");

    const phases = [
      {
        title:"MVP", sub:"Works Today", tag:"LIVE NOW",
        accent:C.teal, bg:C.tealLt,
        items:[
          { done:true,  text:"Shopify MCP product search & checkout" },
          { done:true,  text:"Stripe test mode payment execution" },
          { done:true,  text:"Local AP2 mandates (HMAC-signed, no merchant dep)" },
          { done:true,  text:"RFC 9421 request signing (forward-compatible)" },
          { done:true,  text:"Profile stubs: merchant_profiles.json" },
          { done:true,  text:"Order tracking via polling" },
          { done:true,  text:"Full HITL safety gates + audit log" },
        ],
        unlock:"Shopify MCP + Stripe SDK + Anthropic API"
      },
      {
        title:"PHASE 2", sub:"UCP Merchant Adoption", tag:"UNLOCKED BY MERCHANTS",
        accent:C.indigo, bg:C.indigoLt,
        items:[
          { done:false, text:"Real /.well-known/ucp profile discovery" },
          { done:false, text:"Live capability negotiation per merchant" },
          { done:false, text:"UCPRestClient auto-activates for any UCP merchant" },
          { done:false, text:"Webhook-driven order tracking (replaces polling)" },
          { done:false, text:"Multi-merchant fan-out via MerchantRouter" },
          { done:false, text:"Bidirectional RFC 9421 verification" },
        ],
        unlock:"Merchants publish /.well-known/ucp"
      },
      {
        title:"TARGET", sub:"Full Protocol Stack", tag:"PRODUCTION",
        accent:C.purple, bg:C.purpleLt,
        items:[
          { done:false, text:"AP2 extension: dev.ucp.extensions.ap2 in profiles" },
          { done:false, text:"Verified mandate proofs at merchant side" },
          { done:false, text:"A2A transport (agent-to-agent direct flows)" },
          { done:false, text:"Web / mobile UI + hosted REST API" },
          { done:false, text:"Autonomous replenishment within mandate limits" },
          { done:false, text:"Multi-user profiles + cloud hosting" },
        ],
        unlock:"Full UCP + AP2 extension adoption"
      },
    ];

    const CW = 2.95, CH = 4.56, GAP = 0.24, X0 = 0.36;

    for (let i = 0; i < phases.length; i++) {
      const PH = phases[i];
      const cx = X0 + i * (CW + GAP);

      s.addShape(pres.shapes.RECTANGLE, {
        x:cx, y:0.78, w:CW, h:CH, fill:{color:C.white}, line:{color:PH.accent, pt:2}, shadow:sh()
      });
      // Header
      s.addShape(pres.shapes.RECTANGLE, {
        x:cx, y:0.78, w:CW, h:0.82, fill:{color:PH.accent}, line:{color:PH.accent}
      });
      s.addText(PH.title, {
        x:cx, y:0.78, w:CW, h:0.50,
        fontSize:20, bold:true, color:C.white, charSpacing:2,
        fontFace:"Calibri", align:"center", margin:0
      });
      s.addText(PH.sub, {
        x:cx, y:1.24, w:CW, h:0.30,
        fontSize:9.5, color:"D4EBF8",
        fontFace:"Calibri", align:"center", margin:0
      });
      // Tag
      s.addShape(pres.shapes.RECTANGLE, {
        x:cx, y:1.60, w:CW, h:0.24, fill:{color:PH.bg}, line:{color:PH.bg}
      });
      s.addText(PH.tag, {
        x:cx, y:1.60, w:CW, h:0.24,
        fontSize:8, bold:true, color:PH.accent,
        fontFace:"Calibri", align:"center", margin:0, valign:"middle"
      });

      // Items
      for (let j = 0; j < PH.items.length; j++) {
        const IT = PH.items[j];
        const iy = 1.90 + j * 0.41;
        const iconColor = IT.done ? PH.accent : C.light;
        const icon = IT.done ? "✓" : "○";
        s.addText(icon, {
          x:cx+0.12, y:iy, w:0.24, h:0.36,
          fontSize:11, bold:IT.done, color:iconColor,
          fontFace:"Calibri", valign:"middle", margin:0
        });
        s.addText(IT.text, {
          x:cx+0.38, y:iy, w:CW-0.50, h:0.36,
          fontSize:9.5, color:C.dark, fontFace:"Calibri", valign:"middle", margin:0
        });
        if (j < PH.items.length-1) {
          s.addShape(pres.shapes.LINE, {
            x:cx+0.10, y:iy+0.36, w:CW-0.20, h:0,
            line:{color:C.border, width:0.5}
          });
        }
      }

      // Unlock footer inside card
      s.addShape(pres.shapes.RECTANGLE, {
        x:cx, y:0.78+CH-0.38, w:CW, h:0.04, fill:{color:PH.accent}, line:{color:PH.accent}
      });
      s.addShape(pres.shapes.RECTANGLE, {
        x:cx, y:0.78+CH-0.34, w:CW, h:0.34, fill:{color:PH.bg}, line:{color:PH.bg}
      });
      s.addText(PH.unlock, {
        x:cx+0.10, y:0.78+CH-0.34, w:CW-0.20, h:0.34,
        fontSize:8, color:PH.accent, italic:true,
        fontFace:"Calibri", valign:"middle", margin:0
      });

      // Arrow between phases
      if (i < phases.length-1) {
        const ax = cx + CW;
        const ay = 0.78 + CH/2;
        s.addShape(pres.shapes.LINE, {
          x:ax, y:ay, w:GAP, h:0,
          line:{color:C.mid, width:2, endArrowType:"open"}
        });
      }
    }
  }

  // ══════════════════════════════════════════════════════════
  // SLIDE 10 · PHASE 0 — LET'S BUILD
  // ══════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    s.background = { color: C.navy };

    // Top teal stripe
    s.addShape(pres.shapes.RECTANGLE, {
      x:0, y:0, w:10, h:0.08, fill:{color:C.teal}, line:{color:C.teal}
    });
    // Right decorative circles
    s.addShape(pres.shapes.OVAL, {
      x:7.5, y:-1.2, w:3.5, h:3.5, fill:{color:C.navyMd}, line:{color:C.navyMd}
    });

    s.addText("PHASE 0", {
      x:0.45, y:0.18, w:9, h:0.72,
      fontSize:40, bold:true, color:C.white, charSpacing:6, fontFace:"Calibri"
    });
    s.addText("Foundations — Starting Now", {
      x:0.45, y:0.90, w:7, h:0.44,
      fontSize:18, color:C.tealLt, fontFace:"Calibri"
    });
    s.addText("All testable without Stripe or Shopify  ·  No external API dependencies  ·  Pure Python logic + unit tests", {
      x:0.45, y:1.36, w:8.5, h:0.30,
      fontSize:11, color:"5C8AAD", fontFace:"Calibri"
    });

    const tasks = [
      { n:"1", label:"models/",              accent:C.teal,
        desc:"Pydantic v2 schemas: UCPProfile · ProductResult · CheckoutSession · PurchaseOrder · AgentMandate · UserProfile" },
      { n:"2", label:"storage/",             accent:C.green,
        desc:"TinyDB wrapper tables: mandates · orders · audit_log · spend_records · profile_cache" },
      { n:"3", label:"ucp/signing.py",        accent:C.indigo,
        desc:"RFC 9421 HTTP Message Signatures + JWK key generation — forward-compatible, implemented now at zero cost" },
      { n:"4", label:"ucp/ap2_extension.py", accent:C.purple,
        desc:"AP2MandateEngine: create / verify / revoke / record_spend — fully local HMAC-SHA256, no merchant dependency" },
      { n:"5", label:"guardrails/",          accent:C.amber,
        desc:"SpendingLimiter · VendorGate · ConfidenceChecker — independent of agents, fully unit-testable" },
      { n:"6", label:"config/",              accent:"9D174D",
        desc:"settings.py (typed config, model constants, spending defaults) · .env.example (all vars documented)" },
    ];

    const TH = 0.50, TW_L = 4.45, GAP_H = 0.10;
    const TX0 = 0.40, TX1 = 5.15;
    const TY0 = 1.78;

    for (let i = 0; i < tasks.length; i++) {
      const T = tasks[i];
      const col = i < 3 ? 0 : 1;
      const row = i % 3;
      const tx = col === 0 ? TX0 : TX1;
      const ty = TY0 + row * (TH + GAP_H);

      s.addShape(pres.shapes.RECTANGLE, {
        x:tx, y:ty, w:TW_L, h:TH, fill:{color:"192F4D"}, line:{color:T.accent, pt:1}
      });
      s.addShape(pres.shapes.RECTANGLE, {
        x:tx, y:ty, w:0.10, h:TH, fill:{color:T.accent}, line:{color:T.accent}
      });
      s.addShape(pres.shapes.OVAL, {
        x:tx+0.15, y:ty+0.08, w:0.32, h:0.32,
        fill:{color:T.accent}, line:{color:T.accent}
      });
      s.addText(T.n, {
        x:tx+0.15, y:ty+0.08, w:0.32, h:0.32,
        fontSize:11, bold:true, color:C.white,
        fontFace:"Calibri", align:"center", valign:"middle", margin:0
      });
      s.addText(T.label, {
        x:tx+0.54, y:ty+0.03, w:TW_L-0.62, h:0.24,
        fontSize:11.5, bold:true, color:C.white, fontFace:"Calibri", valign:"middle", margin:0
      });
      s.addText(T.desc, {
        x:tx+0.54, y:ty+0.25, w:TW_L-0.62, h:0.24,
        fontSize:8.5, color:"7A9CC0", fontFace:"Calibri", valign:"middle", margin:0
      });
    }

    // CTA bar
    s.addShape(pres.shapes.RECTANGLE, {
      x:0, y:5.20, w:10, h:0.425, fill:{color:C.teal}, line:{color:C.teal}
    });
    s.addText("Unit tests for all Phase 0 components  ·  No Stripe  ·  No Shopify  ·  Just clean Python logic you can verify immediately", {
      x:0.4, y:5.20, w:9.2, h:0.425,
      fontSize:11.5, bold:true, color:C.white, fontFace:"Calibri", valign:"middle", margin:0
    });
  }

  // ── WRITE ─────────────────────────────────────────────────
  const outPath = "/Users/tolualamu/Desktop/Startups and Entrepreneurship/Agentic Commerce/Agentic_Commerce_Architecture.pptx";
  await pres.writeFile({ fileName: outPath });
  console.log("Written:", outPath);
}

build().catch(e => { console.error(e); process.exit(1); });
