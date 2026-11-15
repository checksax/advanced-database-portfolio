/**
 * Populate 400 sample responses for:
 *   "MPH study on AMR among students in NSUK"
 *   https://docs.google.com/forms/d/14MgVoxPnccrIXS7FszIHR1omcL7n2DZftS5Gvq_juzE/edit
 *
 * HOW TO USE
 *   1. Open the form in edit mode > ⋮ (More) > Apps Script.
 *   2. Paste this whole file into Code.gs and save.
 *   3. Select `populateResponses` and click Run (authorise when asked).
 *   4. If it stops before 400 (Apps Script has a 6-minute limit), just click
 *      Run again — it continues from where it stopped.
 *   5. To start over from scratch, run `resetProgress` first.
 *
 * DISTRIBUTIONS (exact counts out of 400)
 *   Age:        21-25 = 200, 16-20 = 130, 26-30 = 50, Above 30 = 20
 *   Allowance:  Under #20,000 = 160, #20,000-#50,000 = 120,
 *               #51,000-#100,000 = 60, Above #100,000 = 60 (15%)
 *   Level:      400 = 130, 300 = 100, 100 = 80, 200 + 500 = 90 split at random
 *   Faculty:    80 each for the five faculties
 *   Every other question in the form is answered at random.
 */

var FORM_ID = '14MgVoxPnccrIXS7FszIHR1omcL7n2DZftS5Gvq_juzE';
var TOTAL_RESPONSES = 400;
var TIME_LIMIT_MS = 5 * 60 * 1000; // stop safely before the 6-minute limit

// Controlled questions: how to recognise the title, and the count for each option.
// Option keys are matched against the form's choices after removing spaces,
// punctuation and case, so "16 -20 years" matches "16-20 years", etc.
function buildControlledSpecs_() {
  // "Rest at random" for 200 and 500 level: split the remaining 90 randomly,
  // keeping each below 100 level's 80.
  var level200 = 30 + Math.floor(Math.random() * 31); // 30..60
  var level500 = 90 - level200;                        // 30..60

  return [
    {
      key: 'age',
      matches: function (t) { return /^age\b/.test(t); },
      counts: {
        '21-25 years': 200,
        '16-20 years': 130,
        '26-30 years': 50,
        'Above 30 years': 20
      }
    },
    {
      key: 'faculty',
      matches: function (t) { return t.indexOf('faculty') === 0; },
      counts: {
        'Faculty of Social Sciences': 80,
        'Faculty of Administration': 80,
        'Faculty of Arts': 80,
        'Faculty of Sciences': 80,
        'Faculty of Education': 80
      }
    },
    {
      key: 'level',
      matches: function (t) { return t.indexOf('level of study') !== -1; },
      counts: {
        '400 level': 130,
        '300 level': 100,
        '100 level': 80,
        '200 level': level200,
        '500 level': level500
      }
    },
    {
      key: 'allowance',
      matches: function (t) { return t.indexOf('allowance') !== -1 || t.indexOf('pocket money') !== -1; },
      counts: {
        'Under #20,000': 160,
        '#20,000-#50,000': 120,
        '#51,000-#100,000': 60,
        'Above #100,000': 60
      }
    }
  ];
}

// ---------------------------------------------------------------------------

function populateResponses() {
  var start = Date.now();
  var form = FormApp.openById(FORM_ID);
  var props = PropertiesService.getScriptProperties();
  var items = form.getItems();

  // Build (or reload) the plan so exact counts hold across multiple runs.
  var plan = props.getProperty('PLAN');
  if (!plan) {
    plan = JSON.stringify(buildPlan_(items));
    props.setProperty('PLAN', plan);
    props.setProperty('NEXT_INDEX', '0');
  }
  plan = JSON.parse(plan);
  var next = parseInt(props.getProperty('NEXT_INDEX') || '0', 10);

  while (next < TOTAL_RESPONSES) {
    if (Date.now() - start > TIME_LIMIT_MS) {
      Logger.log('Time limit reached at ' + next + ' responses. Run populateResponses again to continue.');
      return;
    }
    submitOne_(form, items, plan, next);
    next++;
    props.setProperty('NEXT_INDEX', String(next));
  }

  Logger.log('Done: ' + TOTAL_RESPONSES + ' responses submitted.');
  logSummary_(plan);
}

/** Clears saved progress so the next run starts a fresh set of 400. */
function resetProgress() {
  PropertiesService.getScriptProperties().deleteProperty('PLAN');
  PropertiesService.getScriptProperties().deleteProperty('NEXT_INDEX');
  Logger.log('Progress reset.');
}

/** Optional: deletes ALL existing responses in the form. Use with care. */
function deleteAllResponses() {
  FormApp.openById(FORM_ID).deleteAllResponses();
  Logger.log('All responses deleted.');
}

// ---------------------------------------------------------------------------

// Returns { itemId: [choiceValue x 400 shuffled] } for each controlled question.
function buildPlan_(items) {
  var specs = buildControlledSpecs_();
  var plan = {};

  specs.forEach(function (spec) {
    var item = findItem_(items, spec.matches);
    if (!item) {
      throw new Error('Could not find the "' + spec.key + '" question in the form.');
    }
    var choices = getChoices_(item);
    var pool = [];
    Object.keys(spec.counts).forEach(function (label) {
      var value = matchChoice_(choices, label);
      if (value === null) {
        throw new Error('Option "' + label + '" not found in "' + item.getTitle() +
          '". Available: ' + choices.join(' | '));
      }
      for (var i = 0; i < spec.counts[label]; i++) pool.push(value);
    });
    if (pool.length !== TOTAL_RESPONSES) {
      throw new Error(spec.key + ' counts add up to ' + pool.length + ', expected ' + TOTAL_RESPONSES);
    }
    plan[String(item.getId())] = shuffle_(pool);
  });

  return plan;
}

function submitOne_(form, items, plan, index) {
  var response = form.createResponse();

  items.forEach(function (item) {
    var id = String(item.getId());
    var r = null;

    if (plan[id]) {
      r = makeChoiceResponse_(item, plan[id][index]);
    } else {
      r = randomResponse_(item);
    }
    if (r) response.withItemResponse(r);
  });

  response.submit();
}

function makeChoiceResponse_(item, value) {
  switch (item.getType()) {
    case FormApp.ItemType.MULTIPLE_CHOICE: return item.asMultipleChoiceItem().createResponse(value);
    case FormApp.ItemType.LIST:            return item.asListItem().createResponse(value);
    case FormApp.ItemType.CHECKBOX:        return item.asCheckboxItem().createResponse([value]);
  }
  return null;
}

// Random answers for every other question type.
function randomResponse_(item) {
  var T = FormApp.ItemType;
  switch (item.getType()) {
    case T.MULTIPLE_CHOICE: {
      var mc = item.asMultipleChoiceItem();
      return mc.createResponse(pick_(choiceValues_(mc.getChoices())));
    }
    case T.LIST: {
      var li = item.asListItem();
      return li.createResponse(pick_(choiceValues_(li.getChoices())));
    }
    case T.CHECKBOX: {
      var cb = item.asCheckboxItem();
      var vals = choiceValues_(cb.getChoices());
      var n = 1 + Math.floor(Math.random() * Math.min(3, vals.length));
      return cb.createResponse(shuffle_(vals.slice()).slice(0, n));
    }
    case T.SCALE: {
      var sc = item.asScaleItem();
      var lo = sc.getLowerBound(), hi = sc.getUpperBound();
      return sc.createResponse(lo + Math.floor(Math.random() * (hi - lo + 1)));
    }
    case T.GRID: {
      var g = item.asGridItem();
      var cols = g.getColumns();
      return g.createResponse(g.getRows().map(function () { return pick_(cols); }));
    }
    case T.CHECKBOX_GRID: {
      var cg = item.asCheckboxGridItem();
      var ccols = cg.getColumns();
      return cg.createResponse(cg.getRows().map(function () { return [pick_(ccols)]; }));
    }
    case T.TEXT:
      return item.asTextItem().createResponse(pick_(SHORT_TEXT_));
    case T.PARAGRAPH_TEXT:
      return item.asParagraphTextItem().createResponse(pick_(LONG_TEXT_));
    case T.DATE:
      return item.asDateItem().createResponse(randomDate_());
    case T.TIME:
      return item.asTimeItem().createResponse(8 + Math.floor(Math.random() * 10), Math.floor(Math.random() * 60));
  }
  return null; // section headers, page breaks, images, etc.
}

// ---------------------------------------------------------------------------
// Helpers

var SHORT_TEXT_ = ['Yes', 'No', 'Not sure', 'Sometimes', 'Rarely', 'Often', 'Instagram', 'WhatsApp', 'TikTok', 'X (Twitter)', 'Facebook'];
var LONG_TEXT_ = [
  'More awareness campaigns on social media would help students.',
  'Students should be taught to complete their antibiotic doses.',
  'Pharmacies should stop selling antibiotics without prescription.',
  'The school clinic should run regular health talks on antibiotic use.',
  'I learnt a lot about antibiotic resistance from online posts.',
  'No comment.'
];

function findItem_(items, matches) {
  for (var i = 0; i < items.length; i++) {
    var t = items[i].getTitle().toLowerCase().trim();
    if (matches(t)) return items[i];
  }
  return null;
}

function getChoices_(item) {
  var T = FormApp.ItemType;
  switch (item.getType()) {
    case T.MULTIPLE_CHOICE: return choiceValues_(item.asMultipleChoiceItem().getChoices());
    case T.LIST:            return choiceValues_(item.asListItem().getChoices());
    case T.CHECKBOX:        return choiceValues_(item.asCheckboxItem().getChoices());
  }
  throw new Error('"' + item.getTitle() + '" is not a choice question.');
}

function choiceValues_(choices) {
  return choices.map(function (c) { return c.getValue(); });
}

function norm_(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9]/g, '');
}

function matchChoice_(choices, label) {
  var target = norm_(label);
  for (var i = 0; i < choices.length; i++) {
    if (norm_(choices[i]) === target) return choices[i];
  }
  return null;
}

function pick_(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function shuffle_(arr) {
  for (var i = arr.length - 1; i > 0; i--) {
    var j = Math.floor(Math.random() * (i + 1));
    var tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;
  }
  return arr;
}

function randomDate_() {
  var start = new Date(2024, 0, 1).getTime();
  var end = new Date().getTime();
  return new Date(start + Math.random() * (end - start));
}

function logSummary_(plan) {
  Object.keys(plan).forEach(function (id) {
    var counts = {};
    plan[id].forEach(function (v) { counts[v] = (counts[v] || 0) + 1; });
    Logger.log(id + ': ' + JSON.stringify(counts));
  });
}
