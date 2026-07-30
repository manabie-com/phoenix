/**
 * @generated SignedSource<<1c598c23bd4e58592d30565d79a58615>>
 * @lightSyntaxTransform
 */

/* tslint:disable */
/* eslint-disable */
// @ts-nocheck

import { ConcreteRequest } from 'relay-runtime';
import { FragmentRefs } from "relay-runtime";
export type TimeBinScale = "DAY" | "HOUR" | "MINUTE" | "MONTH" | "WEEK" | "YEAR";
export type TimeRange = {
  end?: string | null;
  start?: string | null;
};
export type TimeBinConfig = {
  scale?: TimeBinScale;
  utcOffsetMinutes?: number;
};
export type ProjectAnnotationMetricsTraceQuery$variables = {
  projectId: string;
  timeBinConfig: TimeBinConfig;
  timeRange: TimeRange;
};
export type ProjectAnnotationMetricsTraceQuery$data = {
  readonly project: {
    readonly traceAnnotationMetricsTimeSeries?: {
      readonly data: ReadonlyArray<{
        readonly annotationSummaries: ReadonlyArray<{
          readonly labelFractions: ReadonlyArray<{
            readonly fraction: number;
            readonly label: string;
          }>;
          readonly meanScore: number | null;
          readonly name: string;
        }>;
        readonly timestamp: string;
      }>;
    };
    readonly " $fragmentSpreads": FragmentRefs<"ProjectAnnotationMetricsConfigFragment">;
  };
};
export type ProjectAnnotationMetricsTraceQuery = {
  response: ProjectAnnotationMetricsTraceQuery$data;
  variables: ProjectAnnotationMetricsTraceQuery$variables;
};

const node: ConcreteRequest = (function(){
var v0 = {
  "defaultValue": null,
  "kind": "LocalArgument",
  "name": "projectId"
},
v1 = {
  "defaultValue": null,
  "kind": "LocalArgument",
  "name": "timeBinConfig"
},
v2 = {
  "defaultValue": null,
  "kind": "LocalArgument",
  "name": "timeRange"
},
v3 = [
  {
    "kind": "Variable",
    "name": "id",
    "variableName": "projectId"
  }
],
v4 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "name",
  "storageKey": null
},
v5 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "label",
  "storageKey": null
},
v6 = {
  "alias": null,
  "args": [
    {
      "kind": "Variable",
      "name": "timeBinConfig",
      "variableName": "timeBinConfig"
    },
    {
      "kind": "Variable",
      "name": "timeRange",
      "variableName": "timeRange"
    }
  ],
  "concreteType": "AnnotationMetricsTimeSeries",
  "kind": "LinkedField",
  "name": "traceAnnotationMetricsTimeSeries",
  "plural": false,
  "selections": [
    {
      "alias": null,
      "args": null,
      "concreteType": "AnnotationMetricsTimeSeriesDataPoint",
      "kind": "LinkedField",
      "name": "data",
      "plural": true,
      "selections": [
        {
          "alias": null,
          "args": null,
          "kind": "ScalarField",
          "name": "timestamp",
          "storageKey": null
        },
        {
          "alias": null,
          "args": null,
          "concreteType": "AnnotationSummary",
          "kind": "LinkedField",
          "name": "annotationSummaries",
          "plural": true,
          "selections": [
            (v4/*:: as any*/),
            {
              "alias": null,
              "args": null,
              "kind": "ScalarField",
              "name": "meanScore",
              "storageKey": null
            },
            {
              "alias": null,
              "args": null,
              "concreteType": "LabelFraction",
              "kind": "LinkedField",
              "name": "labelFractions",
              "plural": true,
              "selections": [
                (v5/*:: as any*/),
                {
                  "alias": null,
                  "args": null,
                  "kind": "ScalarField",
                  "name": "fraction",
                  "storageKey": null
                }
              ],
              "storageKey": null
            }
          ],
          "storageKey": null
        }
      ],
      "storageKey": null
    }
  ],
  "storageKey": null
},
v7 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "__typename",
  "storageKey": null
},
v8 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "optimizationDirection",
  "storageKey": null
},
v9 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "lowerBound",
  "storageKey": null
},
v10 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "upperBound",
  "storageKey": null
},
v11 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "id",
  "storageKey": null
};
return {
  "fragment": {
    "argumentDefinitions": [
      (v0/*:: as any*/),
      (v1/*:: as any*/),
      (v2/*:: as any*/)
    ],
    "kind": "Fragment",
    "metadata": null,
    "name": "ProjectAnnotationMetricsTraceQuery",
    "selections": [
      {
        "alias": "project",
        "args": (v3/*:: as any*/),
        "concreteType": null,
        "kind": "LinkedField",
        "name": "node",
        "plural": false,
        "selections": [
          {
            "kind": "InlineFragment",
            "selections": [
              {
                "args": null,
                "kind": "FragmentSpread",
                "name": "ProjectAnnotationMetricsConfigFragment"
              },
              (v6/*:: as any*/)
            ],
            "type": "Project",
            "abstractKey": null
          }
        ],
        "storageKey": null
      }
    ],
    "type": "Query",
    "abstractKey": null
  },
  "kind": "Request",
  "operation": {
    "argumentDefinitions": [
      (v0/*:: as any*/),
      (v2/*:: as any*/),
      (v1/*:: as any*/)
    ],
    "kind": "Operation",
    "name": "ProjectAnnotationMetricsTraceQuery",
    "selections": [
      {
        "alias": "project",
        "args": (v3/*:: as any*/),
        "concreteType": null,
        "kind": "LinkedField",
        "name": "node",
        "plural": false,
        "selections": [
          (v7/*:: as any*/),
          {
            "kind": "InlineFragment",
            "selections": [
              {
                "alias": null,
                "args": [
                  {
                    "kind": "Literal",
                    "name": "first",
                    "value": 100
                  }
                ],
                "concreteType": "AnnotationConfigConnection",
                "kind": "LinkedField",
                "name": "annotationConfigs",
                "plural": false,
                "selections": [
                  {
                    "alias": null,
                    "args": null,
                    "concreteType": "AnnotationConfigEdge",
                    "kind": "LinkedField",
                    "name": "edges",
                    "plural": true,
                    "selections": [
                      {
                        "alias": "config",
                        "args": null,
                        "concreteType": null,
                        "kind": "LinkedField",
                        "name": "node",
                        "plural": false,
                        "selections": [
                          (v7/*:: as any*/),
                          {
                            "kind": "InlineFragment",
                            "selections": [
                              (v4/*:: as any*/),
                              {
                                "alias": null,
                                "args": null,
                                "kind": "ScalarField",
                                "name": "annotationType",
                                "storageKey": null
                              }
                            ],
                            "type": "AnnotationConfigBase",
                            "abstractKey": "__isAnnotationConfigBase"
                          },
                          {
                            "kind": "InlineFragment",
                            "selections": [
                              (v8/*:: as any*/),
                              {
                                "alias": null,
                                "args": null,
                                "concreteType": "CategoricalAnnotationValue",
                                "kind": "LinkedField",
                                "name": "values",
                                "plural": true,
                                "selections": [
                                  (v5/*:: as any*/),
                                  {
                                    "alias": null,
                                    "args": null,
                                    "kind": "ScalarField",
                                    "name": "score",
                                    "storageKey": null
                                  }
                                ],
                                "storageKey": null
                              }
                            ],
                            "type": "CategoricalAnnotationConfig",
                            "abstractKey": null
                          },
                          {
                            "kind": "InlineFragment",
                            "selections": [
                              (v8/*:: as any*/),
                              (v9/*:: as any*/),
                              (v10/*:: as any*/)
                            ],
                            "type": "ContinuousAnnotationConfig",
                            "abstractKey": null
                          },
                          {
                            "kind": "InlineFragment",
                            "selections": [
                              (v8/*:: as any*/),
                              {
                                "alias": null,
                                "args": null,
                                "kind": "ScalarField",
                                "name": "threshold",
                                "storageKey": null
                              },
                              (v9/*:: as any*/),
                              (v10/*:: as any*/)
                            ],
                            "type": "FreeformAnnotationConfig",
                            "abstractKey": null
                          },
                          {
                            "kind": "InlineFragment",
                            "selections": [
                              (v11/*:: as any*/)
                            ],
                            "type": "Node",
                            "abstractKey": "__isNode"
                          }
                        ],
                        "storageKey": null
                      }
                    ],
                    "storageKey": null
                  }
                ],
                "storageKey": "annotationConfigs(first:100)"
              },
              (v6/*:: as any*/)
            ],
            "type": "Project",
            "abstractKey": null
          },
          (v11/*:: as any*/)
        ],
        "storageKey": null
      }
    ]
  },
  "params": {
    "cacheID": "c73332e1346754cfb4b4f6e62ef0b4f0",
    "id": null,
    "metadata": {},
    "name": "ProjectAnnotationMetricsTraceQuery",
    "operationKind": "query",
    "text": "query ProjectAnnotationMetricsTraceQuery(\n  $projectId: ID!\n  $timeRange: TimeRange!\n  $timeBinConfig: TimeBinConfig!\n) {\n  project: node(id: $projectId) {\n    __typename\n    ... on Project {\n      ...ProjectAnnotationMetricsConfigFragment\n      traceAnnotationMetricsTimeSeries(timeRange: $timeRange, timeBinConfig: $timeBinConfig) {\n        data {\n          timestamp\n          annotationSummaries {\n            name\n            meanScore\n            labelFractions {\n              label\n              fraction\n            }\n          }\n        }\n      }\n    }\n    id\n  }\n}\n\nfragment ProjectAnnotationMetricsConfigFragment on Project {\n  annotationConfigs(first: 100) {\n    edges {\n      config: node {\n        __typename\n        ... on AnnotationConfigBase {\n          __isAnnotationConfigBase: __typename\n          name\n          annotationType\n        }\n        ... on CategoricalAnnotationConfig {\n          optimizationDirection\n          values {\n            label\n            score\n          }\n        }\n        ... on ContinuousAnnotationConfig {\n          optimizationDirection\n          lowerBound\n          upperBound\n        }\n        ... on FreeformAnnotationConfig {\n          optimizationDirection\n          threshold\n          lowerBound\n          upperBound\n        }\n        ... on Node {\n          __isNode: __typename\n          id\n        }\n      }\n    }\n  }\n}\n"
  }
};
})();

(node as any).hash = "0dab917651a50b77896cb633471385bd";

export default node;
