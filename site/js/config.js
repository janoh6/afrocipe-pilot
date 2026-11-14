/* Public links. Form ids are also in ../config.json (the build side). */
var SITE = {
  target: 5,
  contact: "hello@afrocipe.com",
  forms: {
    submit: { en: "RWdKp4", fr: "VpQ1VJ", sw: "Ge9BVL" },
    report: { en: "GeKvWe", fr: "PR1Bl5", sw: "RWMBQ4" },
    signup: { en: "QoeBd8", fr: "J1pkL4", sw: "OQ4BZY" }
  },
  formUrl: function (kind, lang) { return "https://tally.so/r/" + SITE.forms[kind][lang]; }
};
