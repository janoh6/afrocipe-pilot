/* Public links. Form ids are also in ../config.json (the build side). */
var SITE = {
  target: 5,
  contact: "hello@afrocipe.com",
  forms: {
    submit: { en: "RWdKp4", fr: "VpQ1VJ", sw: "Ge9BVL" },
    report: { en: "GeKvWe", fr: "PR1Bl5", sw: "RWMBQ4" },
    signup: { en: "QoeBd8", fr: "J1pkL4", sw: "OQ4BZY" }
  },
  guides: {
    testers: {
      en: "https://drive.google.com/file/d/1h4OpuIICEoNFBMJkq1m3q2kesYvvtpdr/view",
      fr: "https://drive.google.com/file/d/1lnNDbo0b-_oPyn0hYCCrLSts558yTmXr/view",
      sw: "https://drive.google.com/file/d/1UjjhvjQhJCMZEBviXFRa64SDTMBNjR8t/view"
    },
    writers: {
      en: "https://drive.google.com/file/d/1d87iOjs13wytZTXRdNF-2xMit7kKHjab/view",
      fr: "https://drive.google.com/file/d/1iJ4lWEW3KN8WwXkVyPmwhi4qTNhNW0Bb/view",
      sw: "https://drive.google.com/file/d/14892nIW4ItGdUKvba1NCQPMjTE0HXPkK/view"
    }
  },
  formUrl: function (kind, lang) { return "https://tally.so/r/" + SITE.forms[kind][lang]; }
};
