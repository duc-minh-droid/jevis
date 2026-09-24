window.JEVIS_TRACES = {
 "generated": "2026-09-24 09:02",
 "skills": [
  {
   "name": "open_and_write",
   "pattern": "^open (my default browser|windows terminal|windows explorer|microsoft paint|default browser|command prompt|google chrome|file explorer|the terminal|command line|the browser|web browser|my terminal|calculator|my browser|powershell|notepad\\+\\+|note pad|ms paint|internet|terminal|explorer|my files|notepad|browser|console|chrome|paint|files|calc|edge|cmd)(?: and | then |,? )(?:write|type|put|say) (.+)$"
  },
  {
   "name": "open",
   "pattern": "^(?:open|launch|start) (my default browser|windows terminal|windows explorer|microsoft paint|default browser|command prompt|google chrome|file explorer|the terminal|command line|the browser|web browser|my terminal|calculator|my browser|powershell|notepad\\+\\+|note pad|ms paint|internet|terminal|explorer|my files|notepad|browser|console|chrome|paint|files|calc|edge|cmd)\\.?$"
  },
  {
   "name": "write",
   "pattern": "^(?:write|type|put) (.+)$"
  },
  {
   "name": "save",
   "pattern": "^save(?: (?:it|this|the file|that))?\\.?$"
  },
  {
   "name": "close_tab",
   "pattern": "^close(?: the)? tab\\.?$"
  },
  {
   "name": "new_tab",
   "pattern": "^(?:new|open a new) tab\\.?$"
  }
 ],
 "apps": [
  "browser",
  "calc",
  "explorer",
  "mspaint",
  "notepad",
  "terminal"
 ],
 "traces": [
  {
   "instruction": "open calculator",
   "normalised": "open calculator",
   "ok": true,
   "tier": "skill",
   "skill": "open",
   "attempts": 1,
   "elapsed": 1.618,
   "error": null,
   "events": [
    {
     "t": 0.001,
     "kind": "match",
     "tier": "skill",
     "skill": "open",
     "steps": [
      "launch calc"
     ]
    },
    {
     "t": 0.001,
     "kind": "step",
     "index": 1,
     "tool": "launch",
     "label": "launch calc"
    },
    {
     "t": 1.618,
     "kind": "target",
     "index": 1,
     "rect": [
      292,
      318,
      634,
      830
     ],
     "role": "Window",
     "name": "Calculator",
     "elements": 80,
     "tree": [
      "WINDOW \"Calculator\" (ApplicationFrameWindow)",
      "[1] Window \"Calculator\" -",
      "[2] Button \"Minimize Calculator\" click",
      "[3] Button \"Maximize Calculator\" click",
      "[4] Button \"Close Calculator\" click",
      "[5] Window \"Calculator\" -",
      "[6] Text \"Calculator\" -",
      "[7] Button \"Open Navigation\" click",
      "[8] Text \"Display is 0\" click",
      "[9] Pane \"0\" -",
      "[10] Text \"0\" -",
      "[11] Button \"Open history flyout\" click",
      "[12] Text \"\" -",
      "[13] Group \"Memory controls\" -",
      "[14] Button \"Clear all memory\" click"
     ]
    },
    {
     "t": 1.618,
     "kind": "verify",
     "index": 1,
     "ok": true,
     "clause": "expect",
     "detail": "app"
    },
    {
     "t": 1.618,
     "kind": "result",
     "ok": true,
     "how": "open",
     "elapsed": 1.6181721687316895,
     "error": "",
     "close": true
    }
   ],
   "voice": {
    "fps": 30,
    "levels": [
     0.12,
     0.74,
     0.8,
     0.38,
     0.06,
     0.74,
     0.8,
     0.48,
     0.21,
     0.16,
     0.48,
     0.9,
     1.0,
     0.79,
     0.29,
     0.05,
     0.13,
     0.64,
     0.54,
     0.52,
     0.64,
     0.71,
     0.52,
     0.58,
     0.46,
     0.3,
     0.13,
     0.04
    ]
   }
  },
  {
   "instruction": "open notepad and write hello from jevis",
   "normalised": "open notepad and write hello from jevis",
   "ok": true,
   "tier": "skill",
   "skill": "open_and_write",
   "attempts": 1,
   "elapsed": 2.555,
   "error": null,
   "events": [
    {
     "t": 0.001,
     "kind": "match",
     "tier": "skill",
     "skill": "open_and_write",
     "steps": [
      "launch notepad",
      "type 'hello from jevis'"
     ]
    },
    {
     "t": 0.001,
     "kind": "step",
     "index": 1,
     "tool": "launch",
     "label": "launch notepad"
    },
    {
     "t": 1.7,
     "kind": "target",
     "index": 1,
     "rect": [
      668,
      420,
      1628,
      816
     ],
     "role": "Window",
     "name": "Untitled - Notepad",
     "elements": 45,
     "tree": [
      "WINDOW \"Untitled - Notepad\" (Notepad)",
      "[1] Document \"Text editor\" value='' type",
      "[2] TabItem \"Untitled. Unmodified.\" select",
      "[3] Text \"Untitled\" -",
      "[4] Button \"Close Tab\" click",
      "[5] Text \"\" -",
      "[6] Button \"Add New Tab\" click",
      "[7] Text \"\" -",
      "[8] MenuItem \"File\" click+expand",
      "[9] Button \"File\" click",
      "[10] Text \"File\" -",
      "[11] MenuItem \"Edit\" click+expand",
      "[12] Button \"Edit\" click",
      "[13] Text \"Edit\" -",
      "[14] MenuItem \"View\" click+expand"
     ]
    },
    {
     "t": 1.7,
     "kind": "verify",
     "index": 1,
     "ok": true,
     "clause": "expect",
     "detail": "app"
    },
    {
     "t": 1.7,
     "kind": "step",
     "index": 2,
     "tool": "type_text",
     "label": "type 'hello from jevis'"
    },
    {
     "t": 1.7,
     "kind": "verify",
     "index": 2,
     "ok": true,
     "clause": "require",
     "detail": "editor_empty"
    },
    {
     "t": 1.7,
     "kind": "target",
     "index": 2,
     "rect": [
      674,
      495,
      1622,
      778
     ],
     "role": "Document",
     "name": "Text editor",
     "elements": 45,
     "tree": [
      "WINDOW \"Untitled - Notepad\" (Notepad)",
      "[1] Document \"Text editor\" value='' type",
      "[2] TabItem \"Untitled. Unmodified.\" select",
      "[3] Text \"Untitled\" -",
      "[4] Button \"Close Tab\" click",
      "[5] Text \"\" -",
      "[6] Button \"Add New Tab\" click",
      "[7] Text \"\" -",
      "[8] MenuItem \"File\" click+expand",
      "[9] Button \"File\" click",
      "[10] Text \"File\" -",
      "[11] MenuItem \"Edit\" click+expand",
      "[12] Button \"Edit\" click",
      "[13] Text \"Edit\" -",
      "[14] MenuItem \"View\" click+expand"
     ]
    },
    {
     "t": 2.555,
     "kind": "verify",
     "index": 2,
     "ok": true,
     "clause": "expect",
     "detail": "editor_contains"
    },
    {
     "t": 2.555,
     "kind": "result",
     "ok": true,
     "how": "open_and_write",
     "elapsed": 2.5552549362182617,
     "error": "",
     "close": true
    }
   ],
   "voice": {
    "fps": 30,
    "levels": [
     0.12,
     0.72,
     0.83,
     0.3,
     0.06,
     0.66,
     0.64,
     0.63,
     0.51,
     0.68,
     0.73,
     0.88,
     0.72,
     0.27,
     0.02,
     0.21,
     0.43,
     0.67,
     0.64,
     0.31,
     0.46,
     0.26,
     0.67,
     0.66,
     0.51,
     0.5,
     0.65,
     0.91,
     0.93,
     0.77,
     0.4,
     0.28,
     0.39,
     0.71,
     0.69,
     0.54,
     0.82,
     1.0,
     0.75,
     0.45,
     0.22,
     0.08,
     0.31,
     0.86,
     0.67,
     0.47,
     0.5,
     0.22,
     0.45,
     0.81,
     0.86,
     0.55,
     0.36,
     0.43,
     0.29,
     0.24,
     0.33,
     0.44,
     0.3
    ]
   }
  },
  {
   "instruction": "new tab",
   "normalised": "new tab",
   "ok": true,
   "tier": "skill",
   "skill": "new_tab",
   "attempts": 1,
   "elapsed": 2.459,
   "error": null,
   "events": [
    {
     "t": 0.0,
     "kind": "match",
     "tier": "skill",
     "skill": "new_tab",
     "steps": [
      "menu File > New tab"
     ]
    },
    {
     "t": 0.052,
     "kind": "step",
     "index": 1,
     "tool": "menu",
     "label": "menu File > New tab"
    },
    {
     "t": 0.052,
     "kind": "target",
     "index": 1,
     "rect": [
      678,
      462,
      719,
      494
     ],
     "role": "MenuItem",
     "name": "File",
     "elements": 44,
     "tree": [
      "WINDOW \"*hello from jevis - Notepad\" (Notepad)",
      "[1] Document \"Text editor\" value='hello from jevis' type",
      "[2] TabItem \"hello from jevis. Modified.\" select",
      "[3] Text \"hello from jevis\" -",
      "[4] Text \"\" -",
      "[5] Button \"Add New Tab\" click",
      "[6] Text \"\" -",
      "[7] MenuItem \"File\" click+expand",
      "[8] Button \"File\" click",
      "[9] Text \"File\" -",
      "[10] MenuItem \"Edit\" click+expand",
      "[11] Button \"Edit\" click",
      "[12] Text \"Edit\" -",
      "[13] MenuItem \"View\" click+expand",
      "[14] Button \"View\" click"
     ]
    },
    {
     "t": 2.459,
     "kind": "verify",
     "index": 1,
     "ok": true,
     "clause": "expect",
     "detail": "editor_empty"
    },
    {
     "t": 2.459,
     "kind": "result",
     "ok": true,
     "how": "new_tab",
     "elapsed": 2.4585325717926025,
     "error": "",
     "close": true
    }
   ],
   "voice": {
    "fps": 30,
    "levels": [
     0.22,
     0.75,
     0.91,
     1.0,
     0.9,
     0.37,
     0.23,
     0.54,
     0.72,
     0.79,
     0.52,
     0.36,
     0.17,
     0.12,
     0.11
    ]
   }
  },
  {
   "instruction": "write it reads the UI Automation tree, not pixels",
   "normalised": "write it reads the UI Automation tree, not pixels",
   "ok": true,
   "tier": "skill",
   "skill": "write",
   "attempts": 1,
   "elapsed": 0.941,
   "error": null,
   "events": [
    {
     "t": 0.0,
     "kind": "match",
     "tier": "skill",
     "skill": "write",
     "steps": [
      "type 'it reads the UI Automation tree,'..."
     ]
    },
    {
     "t": 0.068,
     "kind": "step",
     "index": 1,
     "tool": "type_text",
     "label": "type 'it reads the UI Automation tree,'..."
    },
    {
     "t": 0.068,
     "kind": "target",
     "index": 1,
     "rect": [
      674,
      495,
      1622,
      778
     ],
     "role": "Document",
     "name": "Text editor",
     "elements": 48,
     "tree": [
      "WINDOW \"Untitled - Notepad\" (Notepad)",
      "[1] Document \"Text editor\" value='' type",
      "[2] TabItem \"hello from jevis. Modified.\" select",
      "[3] Text \"hello from jevis\" -",
      "[4] Text \"\" -",
      "[5] TabItem \"Untitled. Unmodified.\" select",
      "[6] Text \"Untitled\" -",
      "[7] Button \"Close Tab\" click",
      "[8] Text \"\" -",
      "[9] Button \"Add New Tab\" click",
      "[10] Text \"\" -",
      "[11] MenuItem \"File\" click+expand",
      "[12] Button \"File\" click",
      "[13] Text \"File\" -",
      "[14] MenuItem \"Edit\" click+expand"
     ]
    },
    {
     "t": 0.941,
     "kind": "verify",
     "index": 1,
     "ok": true,
     "clause": "expect",
     "detail": "editor_contains"
    },
    {
     "t": 0.941,
     "kind": "result",
     "ok": true,
     "how": "write",
     "elapsed": 0.941159725189209,
     "error": "",
     "close": true
    }
   ],
   "voice": {
    "fps": 30,
    "levels": [
     0.14,
     0.61,
     0.96,
     0.92,
     0.77,
     0.51,
     0.41,
     0.56,
     0.19,
     0.24,
     0.43,
     0.81,
     0.87,
     0.83,
     0.49,
     0.23,
     0.38,
     0.14,
     0.56,
     0.29,
     0.52,
     0.6,
     0.7,
     0.56,
     0.45,
     0.67,
     0.75,
     0.62,
     0.58,
     0.43,
     0.59,
     0.8,
     0.85,
     0.66,
     0.65,
     0.56,
     0.72,
     0.7,
     0.71,
     0.52,
     0.58,
     0.37,
     0.68,
     0.49,
     0.31,
     0.19,
     0.4,
     0.28,
     0.43,
     0.66,
     0.82,
     0.67,
     0.6,
     0.32,
     0.08,
     0.03,
     0.03,
     0.01,
     0.0,
     0.0,
     0.0,
     0.0,
     0.0,
     0.0,
     0.01,
     0.01,
     0.39,
     0.53,
     0.79,
     1.0,
     0.63,
     0.11,
     0.01,
     0.17,
     0.62,
     0.72,
     0.19,
     0.19,
     0.44,
     0.27,
     0.58,
     0.38,
     0.25,
     0.09,
     0.33,
     0.34,
     0.22,
     0.04
    ]
   }
  }
 ]
};
