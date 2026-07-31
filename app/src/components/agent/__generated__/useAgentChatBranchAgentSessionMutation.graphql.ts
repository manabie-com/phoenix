/**
 * @generated SignedSource<<cc4e17a791783d07d08b820aeb7ba8f6>>
 * @lightSyntaxTransform
 */

/* tslint:disable */
/* eslint-disable */
// @ts-nocheck

import { ConcreteRequest } from 'relay-runtime';
import { FragmentRefs } from "relay-runtime";
export type ModelProvider = "ANTHROPIC" | "AWS" | "AZURE_OPENAI" | "CEREBRAS" | "DEEPSEEK" | "FIREWORKS" | "GOOGLE" | "GROQ" | "MOONSHOT" | "OLLAMA" | "OPENAI" | "PERPLEXITY" | "TOGETHER" | "XAI";
export type OpenAIApiType = "CHAT_COMPLETIONS" | "RESPONSES";
export type BranchAgentSessionInput = {
  id: string;
  messageId: string;
};
export type useAgentChatBranchAgentSessionMutation$variables = {
  connections: ReadonlyArray<string>;
  input: BranchAgentSessionInput;
};
export type useAgentChatBranchAgentSessionMutation$data = {
  readonly branchAgentSession: {
    readonly agentSession: {
      readonly createdAt: string;
      readonly customProviderDeleted: boolean;
      readonly firstInput: string | null;
      readonly id: string;
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
      readonly " $fragmentSpreads": FragmentRefs<"EditAgentSessionTitleDialog_session">;
    };
  };
};
export type useAgentChatBranchAgentSessionMutation = {
  response: useAgentChatBranchAgentSessionMutation$data;
  variables: useAgentChatBranchAgentSessionMutation$variables;
};

const node: ConcreteRequest = (function(){
var v0 = {
  "defaultValue": null,
  "kind": "LocalArgument",
  "name": "connections"
},
v1 = {
  "defaultValue": null,
  "kind": "LocalArgument",
  "name": "input"
},
v2 = [
  {
    "kind": "Variable",
    "name": "input",
    "variableName": "input"
  }
],
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
  "name": "createdAt",
  "storageKey": null
},
v7 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "updatedAt",
  "storageKey": null
},
v8 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "firstInput",
  "storageKey": null
},
v9 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "latestOutput",
  "storageKey": null
},
v10 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "username",
  "storageKey": null
},
v11 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "profilePictureUrl",
  "storageKey": null
},
v12 = {
  "alias": null,
  "args": null,
  "kind": "ScalarField",
  "name": "messages",
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
    {
      "alias": null,
      "args": null,
      "kind": "ScalarField",
      "name": "__typename",
      "storageKey": null
    },
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
};
return {
  "fragment": {
    "argumentDefinitions": [
      (v0/*:: as any*/),
      (v1/*:: as any*/)
    ],
    "kind": "Fragment",
    "metadata": null,
    "name": "useAgentChatBranchAgentSessionMutation",
    "selections": [
      {
        "alias": null,
        "args": (v2/*:: as any*/),
        "concreteType": "BranchAgentSessionMutationPayload",
        "kind": "LinkedField",
        "name": "branchAgentSession",
        "plural": false,
        "selections": [
          {
            "alias": null,
            "args": null,
            "concreteType": "AgentSession",
            "kind": "LinkedField",
            "name": "agentSession",
            "plural": false,
            "selections": [
              (v3/*:: as any*/),
              (v4/*:: as any*/),
              {
                "args": null,
                "kind": "FragmentSpread",
                "name": "EditAgentSessionTitleDialog_session"
              },
              (v5/*:: as any*/),
              (v6/*:: as any*/),
              (v7/*:: as any*/),
              (v8/*:: as any*/),
              (v9/*:: as any*/),
              {
                "alias": null,
                "args": null,
                "concreteType": "User",
                "kind": "LinkedField",
                "name": "user",
                "plural": false,
                "selections": [
                  (v10/*:: as any*/),
                  (v11/*:: as any*/)
                ],
                "storageKey": null
              },
              (v12/*:: as any*/),
              (v14/*:: as any*/),
              (v15/*:: as any*/)
            ],
            "storageKey": null
          }
        ],
        "storageKey": null
      }
    ],
    "type": "Mutation",
    "abstractKey": null
  },
  "kind": "Request",
  "operation": {
    "argumentDefinitions": [
      (v1/*:: as any*/),
      (v0/*:: as any*/)
    ],
    "kind": "Operation",
    "name": "useAgentChatBranchAgentSessionMutation",
    "selections": [
      {
        "alias": null,
        "args": (v2/*:: as any*/),
        "concreteType": "BranchAgentSessionMutationPayload",
        "kind": "LinkedField",
        "name": "branchAgentSession",
        "plural": false,
        "selections": [
          {
            "alias": null,
            "args": null,
            "concreteType": "AgentSession",
            "kind": "LinkedField",
            "name": "agentSession",
            "plural": false,
            "selections": [
              (v3/*:: as any*/),
              (v4/*:: as any*/),
              (v5/*:: as any*/),
              (v6/*:: as any*/),
              (v7/*:: as any*/),
              (v8/*:: as any*/),
              (v9/*:: as any*/),
              {
                "alias": null,
                "args": null,
                "concreteType": "User",
                "kind": "LinkedField",
                "name": "user",
                "plural": false,
                "selections": [
                  (v10/*:: as any*/),
                  (v11/*:: as any*/),
                  (v3/*:: as any*/)
                ],
                "storageKey": null
              },
              (v12/*:: as any*/),
              (v14/*:: as any*/),
              (v15/*:: as any*/)
            ],
            "storageKey": null
          },
          {
            "alias": null,
            "args": null,
            "filters": null,
            "handle": "prependNode",
            "key": "",
            "kind": "LinkedHandle",
            "name": "agentSession",
            "handleArgs": [
              {
                "kind": "Variable",
                "name": "connections",
                "variableName": "connections"
              },
              {
                "kind": "Literal",
                "name": "edgeTypeName",
                "value": "AgentSessionEdge"
              }
            ]
          }
        ],
        "storageKey": null
      }
    ]
  },
  "params": {
    "cacheID": "c4d675a47b95e950234376ec4544bc56",
    "id": null,
    "metadata": {},
    "name": "useAgentChatBranchAgentSessionMutation",
    "operationKind": "mutation",
    "text": "mutation useAgentChatBranchAgentSessionMutation(\n  $input: BranchAgentSessionInput!\n) {\n  branchAgentSession(input: $input) {\n    agentSession {\n      id\n      title\n      ...EditAgentSessionTitleDialog_session\n      isTemporary: isEphemeral\n      createdAt\n      updatedAt\n      firstInput\n      latestOutput\n      user {\n        username\n        profilePictureUrl\n        id\n      }\n      messages\n      model {\n        __typename\n        ... on AgentBuiltinProviderModelSelection {\n          provider\n          modelName\n          openaiApiType\n        }\n        ... on AgentCustomProviderModelSelection {\n          providerId\n          modelName\n        }\n      }\n      customProviderDeleted\n    }\n  }\n}\n\nfragment EditAgentSessionTitleDialog_session on AgentSession {\n  id\n  title\n}\n"
  }
};
})();

(node as any).hash = "5dec309f97b65287e04403fcf1db6a2d";

export default node;
