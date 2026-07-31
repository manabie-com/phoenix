/**
 * @generated SignedSource<<137ab0c7ed4170029e29754ebe1544c3>>
 * @lightSyntaxTransform
 */

/* tslint:disable */
/* eslint-disable */
// @ts-nocheck

import { ConcreteRequest } from 'relay-runtime';
export type ModelProvider = "ANTHROPIC" | "AWS" | "AZURE_OPENAI" | "CEREBRAS" | "DEEPSEEK" | "FIREWORKS" | "GOOGLE" | "GROQ" | "MOONSHOT" | "OLLAMA" | "OPENAI" | "PERPLEXITY" | "TOGETHER" | "XAI";
export type OpenAIApiType = "CHAT_COMPLETIONS" | "RESPONSES";
export type agentSessionRelaySessionQuery$variables = {
  id: string;
};
export type agentSessionRelaySessionQuery$data = {
  readonly agentSession: {
    readonly __typename: "AgentSession";
    readonly createdAt: string;
    readonly customProviderDeleted: boolean;
    readonly firstInput: string | null;
    readonly id: string;
    readonly isActive: boolean;
    readonly isTemporary: boolean;
    readonly latestOutput: string | null;
    readonly messages: any;
    readonly model: {
      readonly __typename: "AgentBuiltinProviderModelSelection";
      readonly modelName: string;
      readonly openaiApiType: OpenAIApiType;
      readonly provider: ModelProvider;
    } | {
      readonly __typename: "AgentCustomProviderModelSelection";
      readonly modelName: string;
      readonly providerId: string;
    } | {
      // This will never be '%other', but we need some
      // value in case none of the concrete values match.
      readonly __typename: "%other";
    };
    readonly title: string;
    readonly updatedAt: string;
    readonly user: {
      readonly profilePictureUrl: string | null;
      readonly username: string;
    } | null;
  } | {
    // This will never be '%other', but we need some
    // value in case none of the concrete values match.
    readonly __typename: "%other";
  };
};
export type agentSessionRelaySessionQuery = {
  response: agentSessionRelaySessionQuery$data;
  variables: agentSessionRelaySessionQuery$variables;
};

const node: ConcreteRequest = (function(){
var v0 = [
  {
    "defaultValue": null,
    "kind": "LocalArgument",
    "name": "id"
  }
],
v1 = [
  {
    "kind": "Variable",
    "name": "id",
    "variableName": "id"
  }
],
v2 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "__typename",
  "storageKey": null
},
v3 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "id",
  "storageKey": null
},
v4 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "title",
  "storageKey": null
},
v5 = {
  "alias": "isTemporary",
  "args": null,
  "kind": "ScalarField",
  "name": "isEphemeral",
  "storageKey": null
},
v6 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "isActive",
  "storageKey": null
},
v7 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "createdAt",
  "storageKey": null
},
v8 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "updatedAt",
  "storageKey": null
},
v9 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "firstInput",
  "storageKey": null
},
v10 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "latestOutput",
  "storageKey": null
},
v11 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "username",
  "storageKey": null
},
v12 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "profilePictureUrl",
  "storageKey": null
},
v13 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "modelName",
  "storageKey": null
},
v14 = {
  "alias": null,
  "args": null,
  "concreteType": null,
  "kind": "LinkedField",
  "name": "model",
  "plural": false,
  "selections": [
    (v2/*:: as any*/),
    {
      "kind": "InlineFragment",
      "selections": [
        {
          "alias": null,
          "args": null,
          "kind": "ScalarField",
          "name": "provider",
          "storageKey": null
        },
        (v13/*:: as any*/),
        {
          "alias": null,
          "args": null,
          "kind": "ScalarField",
          "name": "openaiApiType",
          "storageKey": null
        }
      ],
      "type": "AgentBuiltinProviderModelSelection",
      "abstractKey": null
    },
    {
      "kind": "InlineFragment",
      "selections": [
        {
          "alias": null,
          "args": null,
          "kind": "ScalarField",
          "name": "providerId",
          "storageKey": null
        },
        (v13/*:: as any*/)
      ],
      "type": "AgentCustomProviderModelSelection",
      "abstractKey": null
    }
  ],
  "storageKey": null
},
v15 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "customProviderDeleted",
  "storageKey": null
},
v16 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "messages",
  "storageKey": null
};
return {
  "fragment": {
    "argumentDefinitions": (v0/*:: as any*/),
    "kind": "Fragment",
    "metadata": null,
    "name": "agentSessionRelaySessionQuery",
    "selections": [
      {
        "alias": "agentSession",
        "args": (v1/*:: as any*/),
        "concreteType": null,
        "kind": "LinkedField",
        "name": "node",
        "plural": false,
        "selections": [
          (v2/*:: as any*/),
          {
            "kind": "InlineFragment",
            "selections": [
              (v3/*:: as any*/),
              (v4/*:: as any*/),
              (v5/*:: as any*/),
              (v6/*:: as any*/),
              (v7/*:: as any*/),
              (v8/*:: as any*/),
              (v9/*:: as any*/),
              (v10/*:: as any*/),
              {
                "alias": null,
                "args": null,
                "concreteType": "User",
                "kind": "LinkedField",
                "name": "user",
                "plural": false,
                "selections": [
                  (v11/*:: as any*/),
                  (v12/*:: as any*/)
                ],
                "storageKey": null
              },
              (v14/*:: as any*/),
              (v15/*:: as any*/),
              (v16/*:: as any*/)
            ],
            "type": "AgentSession",
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
    "argumentDefinitions": (v0/*:: as any*/),
    "kind": "Operation",
    "name": "agentSessionRelaySessionQuery",
    "selections": [
      {
        "alias": "agentSession",
        "args": (v1/*:: as any*/),
        "concreteType": null,
        "kind": "LinkedField",
        "name": "node",
        "plural": false,
        "selections": [
          (v2/*:: as any*/),
          (v3/*:: as any*/),
          {
            "kind": "InlineFragment",
            "selections": [
              (v4/*:: as any*/),
              (v5/*:: as any*/),
              (v6/*:: as any*/),
              (v7/*:: as any*/),
              (v8/*:: as any*/),
              (v9/*:: as any*/),
              (v10/*:: as any*/),
              {
                "alias": null,
                "args": null,
                "concreteType": "User",
                "kind": "LinkedField",
                "name": "user",
                "plural": false,
                "selections": [
                  (v11/*:: as any*/),
                  (v12/*:: as any*/),
                  (v3/*:: as any*/)
                ],
                "storageKey": null
              },
              (v14/*:: as any*/),
              (v15/*:: as any*/),
              (v16/*:: as any*/)
            ],
            "type": "AgentSession",
            "abstractKey": null
          }
        ],
        "storageKey": null
      }
    ]
  },
  "params": {
    "cacheID": "ff1c36953bb2c6246c51dd1e0b4ffce9",
    "id": null,
    "metadata": {},
    "name": "agentSessionRelaySessionQuery",
    "operationKind": "query",
    "text": "query agentSessionRelaySessionQuery(\n  $id: ID!\n) {\n  agentSession: node(id: $id) {\n    __typename\n    ... on AgentSession {\n      id\n      title\n      isTemporary: isEphemeral\n      isActive\n      createdAt\n      updatedAt\n      firstInput\n      latestOutput\n      user {\n        username\n        profilePictureUrl\n        id\n      }\n      model {\n        __typename\n        ... on AgentBuiltinProviderModelSelection {\n          provider\n          modelName\n          openaiApiType\n        }\n        ... on AgentCustomProviderModelSelection {\n          providerId\n          modelName\n        }\n      }\n      customProviderDeleted\n      messages\n    }\n    id\n  }\n}\n"
  }
};
})();

(node as any).hash = "36f0e30469a00800044d30a62d7a8e5c";

export default node;
