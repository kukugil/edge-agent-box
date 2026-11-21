// 摘自本机安装的 UI-TARS Desktop 0.2.4（app.asar 的 dist/main/main.js），Apache-2.0，© ByteDance。只作对照参考，不参与运行。

// ---- main.js 第 53755–53800 行 ----
  const initialState = state = createState(setState, getState, api);
  return api;
};
const createStore = (createState) => createState ? createStoreImpl(createState) : createStoreImpl;
const IMAGE_PLACEHOLDER = "<image>";
const MAX_LOOP_COUNT = 100;
const MAX_IMAGE_LENGTH = 5;
const IMAGE_FACTOR = 28;
const MIN_PIXELS = 100 * IMAGE_FACTOR * IMAGE_FACTOR;
const MAX_PIXELS_V1_0 = 2700 * IMAGE_FACTOR * IMAGE_FACTOR;
const MAX_PIXELS_DOUBAO = 5120 * IMAGE_FACTOR * IMAGE_FACTOR;
const MAX_PIXELS_V1_5 = 16384 * IMAGE_FACTOR * IMAGE_FACTOR;
const MAX_RATIO = 200;
var vlm_UITarsModelVersion = /* @__PURE__ */ function(UITarsModelVersion) {
  UITarsModelVersion["V1_0"] = "1.0";
  UITarsModelVersion["V1_5"] = "1.5";
  UITarsModelVersion["DOUBAO_1_5_15B"] = "doubao-1.5-15B";
  UITarsModelVersion["DOUBAO_1_5_20B"] = "doubao-1.5-20B";
  return UITarsModelVersion;
}({});
function _define_property$c(obj, key, value) {
  if (key in obj) Object.defineProperty(obj, key, {
    value,
    enumerable: true,
    configurable: true,
    writable: true
  });
  else obj[key] = value;
  return obj;
}
var agent_ErrorStatusEnum = /* @__PURE__ */ function(ErrorStatusEnum) {
  ErrorStatusEnum[ErrorStatusEnum["SCREENSHOT_RETRY_ERROR"] = -1e5] = "SCREENSHOT_RETRY_ERROR";
  ErrorStatusEnum[ErrorStatusEnum["INVOKE_RETRY_ERROR"] = -100001] = "INVOKE_RETRY_ERROR";
  ErrorStatusEnum[ErrorStatusEnum["EXECUTE_RETRY_ERROR"] = -100002] = "EXECUTE_RETRY_ERROR";
  ErrorStatusEnum[ErrorStatusEnum["MODEL_SERVICE_ERROR"] = -100003] = "MODEL_SERVICE_ERROR";
  ErrorStatusEnum[ErrorStatusEnum["REACH_MAXLOOP_ERROR"] = -100004] = "REACH_MAXLOOP_ERROR";
  ErrorStatusEnum[ErrorStatusEnum["ENVIRONMENT_ERROR"] = -100005] = "ENVIRONMENT_ERROR";
  ErrorStatusEnum[ErrorStatusEnum["UNKNOWN_ERROR"] = -100099] = "UNKNOWN_ERROR";
  return ErrorStatusEnum;
}({});
class GUIAgentError extends Error {
  constructor(status, message, stack) {
    super(message), _define_property$c(this, "status", void 0), _define_property$c(this, "message", void 0), _define_property$c(this, "stack", void 0);
    this.status = status;
    this.message = message;
    this.stack = stack;

// ---- main.js 第 99084–99830 行 ----
function smartResizeForV15(height, width, maxRatio = MAX_RATIO, factor = IMAGE_FACTOR, minPixels = MIN_PIXELS, maxPixels = MAX_PIXELS_V1_5) {
  if (Math.max(height, width) / Math.min(height, width) > maxRatio) {
    console.error(`absolute aspect ratio must be smaller than ${maxRatio}, got ${Math.max(height, width) / Math.min(height, width)}`);
    return null;
  }
  let wBar = Math.max(factor, roundByFactor(width, factor));
  let hBar = Math.max(factor, roundByFactor(height, factor));
  if (hBar * wBar > maxPixels) {
    const beta = Math.sqrt(height * width / maxPixels);
    hBar = floorByFactor(height / beta, factor);
    wBar = floorByFactor(width / beta, factor);
  } else if (hBar * wBar < minPixels) {
    const beta = Math.sqrt(minPixels / (height * width));
    hBar = ceilByFactor(height * beta, factor);
    wBar = ceilByFactor(width * beta, factor);
  }
  return [
    wBar,
    hBar
  ];
}
function actionParser(params) {
  const { prediction, factor, mode: mode2, screenContext, scaleFactor, modelVer } = params;
  const parsed = parseActionVlm(prediction, Array.isArray(factor) ? factor : [
    factor,
    factor
  ], mode2, screenContext, scaleFactor, modelVer);
  return {
    parsed
  };
}
function parseActionVlm(text, factors = [
  1e3,
  1e3
], mode2 = "bc", screenContext, scaleFactor, modelVer = vlm_UITarsModelVersion.V1_0) {
  let reflection = null;
  let thought = null;
  let actionStr = "";
  let smartResizeFactors = null;
  if (modelVer === vlm_UITarsModelVersion.V1_5 && (null == screenContext ? void 0 : screenContext.height) && (null == screenContext ? void 0 : screenContext.width)) smartResizeFactors = smartResizeForV15(screenContext.height, screenContext.width);
  text = text.trim();
  if ("bc" === mode2) {
    if (text.includes("Thought:")) {
      const thoughtMatch = text.match(/Thought: ([\s\S]+?)(?=\s*Action[:：]|$)/);
      if (thoughtMatch) thought = thoughtMatch[1].trim();
    } else if (text.startsWith("Reflection:")) {
      const reflectionMatch = text.match(/Reflection: ([\s\S]+?)Action_Summary: ([\s\S]+?)(?=\s*Action[:：]|$)/);
      if (reflectionMatch) {
        thought = reflectionMatch[2].trim();
        reflection = reflectionMatch[1].trim();
      }
    } else if (text.startsWith("Action_Summary:")) {
      const summaryMatch = text.match(/Action_Summary: (.+?)(?=\s*Action[:：]|$)/);
      if (summaryMatch) thought = summaryMatch[1].trim();
    }
    if ([
      "Action:",
      "Action："
    ].some((keyword2) => text.includes(keyword2))) {
      const actionParts = text.split(/Action[:：]/);
      actionStr = actionParts[actionParts.length - 1];
    } else actionStr = text;
  } else if ("o1" === mode2) {
    const thoughtMatch = text.match(/<Thought>\s*(.*?)\s*<\/Thought>/);
    const actionSummaryMatch = text.match(/\nAction_Summary:\s*(.*?)\s*Action:/);
    const actionMatch = text.match(/\nAction:\s*(.*?)\s*<\/Output>/);
    const thoughtContent = thoughtMatch ? thoughtMatch[1] : null;
    const actionSummaryContent = actionSummaryMatch ? actionSummaryMatch[1] : null;
    const actionContent = actionMatch ? actionMatch[1] : null;
    thought = `${thoughtContent}
<Action_Summary>
${actionSummaryContent}`;
    actionStr = actionContent || "";
  }
  const allActions = actionStr.split("\n\n");
  const actions = [];
  for (const rawStr of allActions) {
    const actionInstance = parseAction(rawStr.replace(/\n/g, String.raw`\n`).trimStart());
    let actionType = "";
    let actionInputs = {};
    if (actionInstance) {
      actionType = actionInstance.function;
      const params = actionInstance.args;
      actionInputs = {};
      for (const [paramName, param] of Object.entries(params)) {
        if (!param) continue;
        const trimmedParam = param.trim();
        if (paramName.includes("start_box") || paramName.includes("end_box")) {
          const oriBox = trimmedParam;
          const numbers = oriBox.replace(/[()[\]]/g, "").split(",").filter((ori) => "" !== ori);
          const floatNumbers = numbers.map((num, idx) => {
            const factorIndex = idx % 2;
            if (modelVer === vlm_UITarsModelVersion.V1_5 && smartResizeFactors) return Number.parseFloat(num) / smartResizeFactors[factorIndex];
            return Number.parseFloat(num) / factors[factorIndex];
          });
          if (2 === floatNumbers.length) floatNumbers.push(floatNumbers[0], floatNumbers[1]);
          actionInputs[paramName.trim()] = JSON.stringify(floatNumbers);
          if ((null == screenContext ? void 0 : screenContext.width) && (null == screenContext ? void 0 : screenContext.height)) {
            const boxKey = paramName.includes("start_box") ? "start_coords" : "end_coords";
            const [x1, y1, x2 = x1, y2 = y1] = floatNumbers;
            const [widthFactor, heightFactor] = factors;
            actionInputs[boxKey] = [
              x1,
              y1,
              x2,
              y2
            ].every(lodash_isnumber) ? [
              Math.round((x1 + x2) / 2 * (null == screenContext ? void 0 : screenContext.width) * widthFactor) / widthFactor * (scaleFactor ?? 1),
              Math.round((y1 + y2) / 2 * (null == screenContext ? void 0 : screenContext.height) * heightFactor) / heightFactor * (scaleFactor ?? 1)
            ] : [];
          }
        } else actionInputs[paramName.trim()] = trimmedParam;
      }
    }
    actions.push({
      reflection,
      thought: thought || "",
      action_type: actionType,
      action_inputs: actionInputs
    });
  }
  return actions;
}
function parseAction(actionStr) {
  try {
    actionStr = actionStr.replace(/<\|box_start\|>|<\|box_end\|>/g, "");
    actionStr = actionStr.replace(/(?<!start_|end_)point=/g, "start_box=").replace(/start_point=/g, "start_box=").replace(/end_point=/g, "end_box=");
    const functionPattern = /^(\w+)\((.*)\)$/;
    const match = actionStr.trim().match(functionPattern);
    if (!match) throw new Error("Not a function call");
    const [_2, functionName, argsStr] = match;
    const kwargs = {};
    if (argsStr.trim()) {
      const argPairs = argsStr.match(/([^,']|'[^']*')+/g) || [];
      for (const pair of argPairs) {
        const [key, ...valueParts] = pair.split("=");
        if (!key) continue;
        let value = valueParts.join("=").trim().replace(/^['"]|['"]$/g, "");
        if (value.includes("<bbox>")) {
          value = value.replace(/<bbox>|<\/bbox>/g, "").replace(/\s+/g, ",");
          value = `(${value})`;
        }
        if (value.includes("<point>")) {
          value = value.replace(/<point>|<\/point>/g, "").replace(/\s+/g, ",");
          value = `(${value})`;
        }
        kwargs[key.trim()] = value;
      }
    }
    return {
      function: functionName,
      args: kwargs
    };
  } catch (e2) {
    console.error(`Failed to parse action '${actionStr}': ${e2}`);
    return null;
  }
}
function _define_property$b(obj, key, value) {
  if (key in obj) Object.defineProperty(obj, key, {
    value,
    enumerable: true,
    configurable: true,
    writable: true
  });
  else obj[key] = value;
  return obj;
}
class BaseGUIAgent {
  constructor(config2) {
    _define_property$b(this, "config", void 0);
    this.config = config2;
    this.config = config2;
  }
}
class BaseModel {
}
class BaseOperator {
}
function _define_property$a(obj, key, value) {
  if (key in obj) Object.defineProperty(obj, key, {
    value,
    enumerable: true,
    configurable: true,
    writable: true
  });
  else obj[key] = value;
  return obj;
}
let Operator$1 = class Operator2 extends BaseOperator {
};
_define_property$a(Operator$1, "MANUAL", void 0);
class Model extends BaseModel {
}
const parseBoxToScreenCoords = ({ boxStr, screenWidth, screenHeight, factors = DEFAULT_FACTORS }) => {
  if (!boxStr) return {
    x: null,
    y: null
  };
  const coords = boxStr.replace("[", "").replace("]", "").split(",").map((num) => parseFloat(num.trim()));
  const [x1, y1, x2 = x1, y2 = y1] = coords;
  const [widthFactor, heightFactor] = factors;
  return {
    x: Math.round((x1 + x2) / 2 * screenWidth * widthFactor) / widthFactor,
    y: Math.round((y1 + y2) / 2 * screenHeight * heightFactor) / heightFactor
  };
};
const processVlmParams = (conversations, images, maxImageLength = MAX_IMAGE_LENGTH) => {
  if (images.length > maxImageLength) {
    const excessCount = images.length - maxImageLength;
    images = images.slice(excessCount);
    let imageCountToRemove = excessCount;
    conversations = conversations.filter((convo) => {
      if (imageCountToRemove > 0 && convo.value === IMAGE_PLACEHOLDER) {
        imageCountToRemove--;
        return false;
      }
      return true;
    });
  }
  return {
    images,
    conversations
  };
};
const toVlmModelFormat = ({ historyMessages, conversations, systemPrompt }) => {
  const USER_INSTRUCTION_MARKER = "## User Instruction";
  const history = formatHistoryMessages(historyMessages);
  return {
    conversations: conversations.map((conv, idx) => {
      if (0 === idx && "human" === conv.from) {
        let newValue = "";
        if (systemPrompt.includes(USER_INSTRUCTION_MARKER)) {
          const insertIndex = systemPrompt.lastIndexOf(USER_INSTRUCTION_MARKER);
          const slicedPrefix = systemPrompt.slice(0, insertIndex);
          const slicedSuffix = systemPrompt.slice(insertIndex);
          newValue = slicedPrefix + (slicedPrefix.endsWith("\n") ? "" : "\n") + history + "\n" + slicedSuffix + (slicedSuffix.endsWith("\n") ? "" : "\n") + conv.value;
        } else newValue = `${systemPrompt}
${history}
${USER_INSTRUCTION_MARKER}
${conv.value}`;
        return {
          from: conv.from,
          value: newValue
        };
      }
      return {
        from: conv.from,
        value: conv.value
      };
    }),
    images: conversations.filter((conv) => conv.value === IMAGE_PLACEHOLDER && !!conv.screenshotBase64).map((conv) => conv.screenshotBase64)
  };
};
const getSummary = (prediction) => prediction.replace(/Reflection:[\s\S]*?(?=Action_Summary:|Action:|$)/g, "").trim();
const convertToOpenAIMessages = ({ conversations, images }) => {
  const messages2 = [];
  let imageIndex = 0;
  conversations.forEach((conv) => {
    if (conv.value === IMAGE_PLACEHOLDER) {
      if (imageIndex < images.length) {
        messages2.push({
          role: "user",
          content: [
            {
              type: "image_url",
              image_url: {
                url: `data:image/png;base64,${images[imageIndex]}`
              }
            }
          ]
        });
        imageIndex++;
      }
    } else messages2.push({
      role: "human" === conv.from ? "user" : "assistant",
      content: conv.value
    });
  });
  return messages2;
};
function replaceBase64Prefix(base642) {
  return base642.replace(/^data:image\/\w+;base64,/, "");
}
async function preprocessResizeImage(image_base64, maxPixels) {
  try {
    const imageBuffer = Buffer.from(image_base64, "base64");
    const image = await Jimp$1.read(imageBuffer);
    const { width, height } = image.bitmap;
    const currentPixels = width * height;
    if (currentPixels > maxPixels) {
      const resizeFactor = Math.sqrt(maxPixels / currentPixels);
      const newWidth = Math.floor(width * resizeFactor);
      const newHeight = Math.floor(height * resizeFactor);
      const resized = await image.resize({
        w: newWidth,
        h: newHeight
      }).getBuffer("image/png", {
        quality: 60
      });
      return resized.toString("base64");
    }
    const base642 = await image.getBase64("image/png", {
      quality: 60
    });
    return replaceBase64Prefix(base642);
  } catch (error2) {
    console.error("preprocessResizeImage error:", error2);
    throw error2;
  }
}
function formatHistoryMessages(messages2) {
  const lastMessages = messages2.slice(-30);
  const lines = lastMessages.map((msg) => {
    const role = "human" === msg.from ? "human" : "assistant";
    return `${role}: ${msg.value}`;
  });
  return "## History Messages\n" + lines.join("\n") + "\n";
}
const convertToResponseApiInput = (messages2) => messages2.map((message) => {
  if (Array.isArray(null == message ? void 0 : message.content) && (null == message ? void 0 : message.content.length) > 0) {
    const content = message.content.map((item) => {
      var _item_image_url;
      if ("image_url" === item.type && (null == (_item_image_url = item.image_url) ? void 0 : _item_image_url.url)) return {
        type: "input_image",
        image_url: item.image_url.url
      };
      return item;
    });
    return {
      role: message.role,
      content
    };
  }
  return message;
});
const isMessageImage = (c2) => "role" in c2 && "user" === c2.role && Array.isArray(c2.content) && c2.content.some((item) => {
  var _item_image_url;
  return "image_url" === item.type && (null == (_item_image_url = item.image_url) ? void 0 : _item_image_url.url) || "input_image" === item.type && item.image_url;
});
function _define_property$9(obj, key, value) {
  if (key in obj) Object.defineProperty(obj, key, {
    value,
    enumerable: true,
    configurable: true,
    writable: true
  });
  else obj[key] = value;
  return obj;
}
class UITarsModel extends Model {
  get useResponsesApi() {
    return this.modelConfig.useResponsesApi ?? false;
  }
  get factors() {
    return DEFAULT_FACTORS;
  }
  get modelName() {
    return this.modelConfig.model ?? "unknown";
  }
  reset() {
    this.headImageContext = null;
  }
  async invokeModelProvider(uiTarsVersion = vlm_UITarsModelVersion.V1_0, params, options2, headers) {
    var _result_choices__message, _result_choices_, _result_choices, _result_usage;
    const { logger: logger2 } = useContext();
    const { messages: messages2, previousResponseId } = params;
    const { baseURL, apiKey, model, max_tokens = uiTarsVersion == vlm_UITarsModelVersion.V1_5 ? 65535 : 1e3, temperature = 0, top_p = 0.7, ...restOptions } = this.modelConfig;
    const openai = new OpenAI$1({
      ...restOptions,
      maxRetries: 0,
      baseURL,
      apiKey
    });
    const createCompletionPrams = {
      model,
      messages: messages2,
      stream: false,
      seed: null,
      stop: null,
      frequency_penalty: null,
      presence_penalty: null,
      max_tokens,
      temperature,
      top_p
    };
    const createCompletionPramsThinkingVp = {
      ...createCompletionPrams,
      thinking: {
        type: "disabled"
      }
    };
    const startTime = Date.now();
    if (this.modelConfig.useResponsesApi) {
      var _this_headImageContext, _this_headImageContext1, _result_usage1;
      const lastAssistantIndex = messages2.findLastIndex((c2) => "assistant" === c2.role);
      logger2.info("[ResponseAPI] lastAssistantIndex: ", lastAssistantIndex);
      const inputs = convertToResponseApiInput(lastAssistantIndex > -1 ? messages2.slice(lastAssistantIndex + 1) : messages2);
      const headImageMessageIndex = messages2.findIndex(isMessageImage);
      if ((null == (_this_headImageContext = this.headImageContext) ? void 0 : _this_headImageContext.responseIds.length) && (null == (_this_headImageContext1 = this.headImageContext) ? void 0 : _this_headImageContext1.messageIndex) !== headImageMessageIndex) {
        logger2.info("[ResponseAPI] should [delete]: ", this.headImageContext, "headImageMessageIndex", headImageMessageIndex);
        const headImageResponseId = this.headImageContext.responseIds.shift();
        if (headImageResponseId) {
          const deletedResponse = await openai.responses.delete(headImageResponseId, {
            headers
          });
          logger2.info("[ResponseAPI] [deletedResponse]: ", headImageResponseId, deletedResponse);
        }
      }
      let result2;
      let responseId = previousResponseId;
      for (const input of inputs) {
        const truncated = JSON.stringify([
          input
        ], (key, value) => {
          if ("string" == typeof value && value.startsWith("data:image/")) return value.slice(0, 50) + "...[truncated]";
          return value;
        }, 2);
        const responseParams = {
          input: [
            input
          ],
          model,
          temperature,
          top_p,
          stream: false,
          max_output_tokens: max_tokens,
          ...responseId && {
            previous_response_id: responseId
          },
          thinking: {
            type: "disabled"
          }
        };
        logger2.info("[ResponseAPI] [input]: ", truncated, "previous_response_id", null == responseParams ? void 0 : responseParams.previous_response_id, "headImageMessageIndex", headImageMessageIndex);
        result2 = await openai.responses.create(responseParams, {
          ...options2,
          timeout: 3e4,
          headers
        });
        logger2.info("[ResponseAPI] [result]: ", result2);
        responseId = null == result2 ? void 0 : result2.id;
        logger2.info("[ResponseAPI] [responseId]: ", responseId);
        if (responseId && isMessageImage(input)) {
          var _this_headImageContext2;
          this.headImageContext = {
            messageIndex: headImageMessageIndex,
            responseIds: [
              ...(null == (_this_headImageContext2 = this.headImageContext) ? void 0 : _this_headImageContext2.responseIds) || [],
              responseId
            ]
          };
        }
        logger2.info("[ResponseAPI] [headImageContext]: ", this.headImageContext);
      }
      return {
        prediction: (null == result2 ? void 0 : result2.output_text) ?? "",
        costTime: Date.now() - startTime,
        costTokens: (null == result2 ? void 0 : null == (_result_usage1 = result2.usage) ? void 0 : _result_usage1.total_tokens) ?? 0,
        responseId
      };
    }
    const result = await openai.chat.completions.create(createCompletionPramsThinkingVp, {
      ...options2,
      timeout: 3e4,
      headers
    });
    return {
      prediction: (null == (_result_choices = result.choices) ? void 0 : null == (_result_choices_ = _result_choices[0]) ? void 0 : null == (_result_choices__message = _result_choices_.message) ? void 0 : _result_choices__message.content) ?? "",
      costTime: Date.now() - startTime,
      costTokens: (null == (_result_usage = result.usage) ? void 0 : _result_usage.total_tokens) ?? 0
    };
  }
  async invoke(params) {
    const { conversations, images, screenContext, scaleFactor, uiTarsVersion, headers, previousResponseId } = params;
    const { logger: logger2, signal } = useContext();
    null == logger2 || logger2.info(`[UITarsModel] invoke: screenContext=${JSON.stringify(screenContext)}, scaleFactor=${scaleFactor}, uiTarsVersion=${uiTarsVersion}, useResponsesApi=${this.modelConfig.useResponsesApi}`);
    const maxPixels = uiTarsVersion === vlm_UITarsModelVersion.V1_5 ? MAX_PIXELS_V1_5 : uiTarsVersion === vlm_UITarsModelVersion.DOUBAO_1_5_15B || uiTarsVersion === vlm_UITarsModelVersion.DOUBAO_1_5_20B ? MAX_PIXELS_DOUBAO : MAX_PIXELS_V1_0;
    const compressedImages = await Promise.all(images.map((image) => preprocessResizeImage(image, maxPixels)));
    const messages2 = convertToOpenAIMessages({
      conversations,
      images: compressedImages
    });
    const startTime = Date.now();
    const result = await this.invokeModelProvider(uiTarsVersion, {
      messages: messages2,
      previousResponseId
    }, {
      signal
    }, headers).catch((e2) => {
      null == logger2 || logger2.error("[UITarsModel] error", e2);
      throw e2;
    }).finally(() => {
      null == logger2 || logger2.info(`[UITarsModel cost]: ${Date.now() - startTime}ms`);
    });
    if (!result.prediction) {
      const err = new Error();
      err.name = "vlm response error";
      err.stack = JSON.stringify(result) ?? "no message";
      null == logger2 || logger2.error(err);
      throw err;
    }
    const { prediction, costTime, costTokens, responseId } = result;
    try {
      const { parsed: parsedPredictions } = actionParser({
        prediction,
        factor: this.factors,
        screenContext,
        scaleFactor,
        modelVer: uiTarsVersion
      });
      return {
        prediction,
        parsedPredictions,
        costTime,
        costTokens,
        responseId
      };
    } catch (error2) {
      null == logger2 || logger2.error("[UITarsModel] error", error2);
      return {
        prediction,
        parsedPredictions: [],
        responseId
      };
    }
  }
  constructor(modelConfig) {
    super(), _define_property$9(this, "modelConfig", void 0), _define_property$9(this, "headImageContext", void 0), this.modelConfig = modelConfig, this.headImageContext = null;
    this.modelConfig = modelConfig;
  }
}
function _define_property$8(obj, key, value) {
  if (key in obj) Object.defineProperty(obj, key, {
    value,
    enumerable: true,
    configurable: true,
    writable: true
  });
  else obj[key] = value;
  return obj;
}
class GUIAgent extends BaseGUIAgent {
  async run(instruction, historyMessages, remoteModelHdrs) {
    const { operator, model, logger: logger2 } = this;
    const { signal, onData, onError, retry: retry2 = {}, maxLoopCount = MAX_LOOP_COUNT } = this.config;
    const currentTime = Date.now();
    const data2 = {
      version: share_ShareVersion.V1,
      systemPrompt: this.systemPrompt,
      instruction,
      modelName: this.model.modelName,
      status: agent_StatusEnum.INIT,
      logTime: currentTime,
      conversations: [
        {
          from: "human",
          value: instruction,
          timing: {
            start: currentTime,
            end: currentTime,
            cost: 0
          }
        }
      ]
    };
    setContext(Object.assign(this.config, {
      logger: this.logger,
      systemPrompt: this.systemPrompt,
      factors: this.model.factors,
      model: this.model
    }));
    logger2.info(`[GUIAgent] run:
system prompt: ${this.systemPrompt},
model version: ${this.uiTarsVersion},
model config: ${JSON.stringify(this.model)}`);
    let loopCnt = 0;
    let snapshotErrCnt = 0;
    let totalTokens = 0;
    let totalTime = 0;
    let previousResponseId;
    data2.status = agent_StatusEnum.RUNNING;
    await (null == onData ? void 0 : onData({
      data: {
        ...data2,
        conversations: []
      }
    }));
    const sessionId = this.generateSessionId();
    try {
      while (true) {
        var _retry_screenshot, _retry_screenshot1, _retry_model, _retry_model1;
        logger2.info("[GUIAgent] loopCnt:", loopCnt);
        if (this.isPaused && this.resumePromise) {
          data2.status = agent_StatusEnum.PAUSE;
          await (null == onData ? void 0 : onData({
            data: {
              ...data2,
              conversations: []
            }
          }));
          await this.resumePromise;
          data2.status = agent_StatusEnum.RUNNING;
          await (null == onData ? void 0 : onData({
            data: {
              ...data2,
              conversations: []
            }
          }));
        }
        if (this.isStopped || data2.status !== agent_StatusEnum.RUNNING && data2.status !== agent_StatusEnum.PAUSE || (null == signal ? void 0 : signal.aborted)) {
          (null == signal ? void 0 : signal.aborted) && (data2.status = agent_StatusEnum.USER_STOPPED);
          break;
        }
        if (loopCnt >= maxLoopCount) {
          Object.assign(data2, {
            status: agent_StatusEnum.ERROR,
            error: this.guiAgentErrorParser(agent_ErrorStatusEnum.REACH_MAXLOOP_ERROR)
          });
          break;
        }
        if (snapshotErrCnt >= MAX_SNAPSHOT_ERR_CNT) {
          Object.assign(data2, {
            status: agent_StatusEnum.ERROR,
            error: this.guiAgentErrorParser(agent_ErrorStatusEnum.SCREENSHOT_RETRY_ERROR)
          });
          break;
        }
        loopCnt += 1;
        const start = Date.now();
        const snapshot = await async_retry(() => operator.screenshot(), {
          retries: (null == retry2 ? void 0 : null == (_retry_screenshot = retry2.screenshot) ? void 0 : _retry_screenshot.maxRetries) ?? 0,
          minTimeout: 5e3,
          onRetry: null == retry2 ? void 0 : null == (_retry_screenshot1 = retry2.screenshot) ? void 0 : _retry_screenshot1.onRetry
        });
        const { width, height, mime: mime2 } = await Jimp$1.fromBuffer(Buffer.from(replaceBase64Prefix(snapshot.base64), "base64")).catch((e2) => {
          logger2.error("[GUIAgent] screenshot error", e2);
          return {
            width: null,
            height: null,
            mime: ""
          };
        });
        const isValidImage = !!((null == snapshot ? void 0 : snapshot.base64) && width && height);
        if (!isValidImage) {
          loopCnt -= 1;
          snapshotErrCnt += 1;
          await sleep$2(1e3);
          continue;
        }
        let end = Date.now();
        if (isValidImage) {
          data2.conversations.push({
            from: "human",
            value: IMAGE_PLACEHOLDER,
            screenshotBase64: snapshot.base64,
            screenshotContext: {
              size: {
                width,
                height
              },
              mime: mime2,
              scaleFactor: snapshot.scaleFactor
            },
            timing: {
              start,
              end,
              cost: end - start
            }
          });
          await (null == onData ? void 0 : onData({
            data: {
              ...data2,
              conversations: data2.conversations.slice(-1)
            }
          }));
        }
        const modelFormat = toVlmModelFormat({
          historyMessages: historyMessages || [],
          conversations: data2.conversations,
          systemPrompt: data2.systemPrompt
        });
        const vlmParams = {
          ...processVlmParams(modelFormat.conversations, modelFormat.images),
          screenContext: {
            width,
            height
          },
          scaleFactor: snapshot.scaleFactor,
          uiTarsVersion: this.uiTarsVersion,
          headers: {
            ...remoteModelHdrs,
            "X-Session-Id": sessionId
          },
          previousResponseId
        };
        const { prediction, parsedPredictions, costTime, costTokens, responseId } = await async_retry(async (bail) => {
          try {
            const result = await model.invoke(vlmParams);
            return result;
          } catch (error2) {
            var _error_message2;
            if (error2 instanceof Error && ((null == error2 ? void 0 : error2.name) === "APIUserAbortError" || (null == error2 ? void 0 : null == (_error_message2 = error2.message) ? void 0 : _error_message2.includes("aborted")))) {
              bail(error2);
              return {
                prediction: "",
                parsedPredictions: []
              };
            }
            Object.assign(data2, {
              status: agent_StatusEnum.ERROR,
              error: this.guiAgentErrorParser(agent_ErrorStatusEnum.INVOKE_RETRY_ERROR, error2)
            });
            throw error2;
          }
        }, {
          retries: (null == retry2 ? void 0 : null == (_retry_model = retry2.model) ? void 0 : _retry_model.maxRetries) ?? 0,
          minTimeout: 3e4,
          onRetry: null == retry2 ? void 0 : null == (_retry_model1 = retry2.model) ? void 0 : _retry_model1.onRetry
        });
        if (responseId) previousResponseId = responseId;
        totalTokens += costTokens || 0;
        totalTime += costTime || 0;
        logger2.info(`[GUIAgent] consumes: >>> costTime: ${costTime}, costTokens: ${costTokens} <<<`);
        logger2.info("[GUIAgent] Response:", prediction);
        logger2.info("[GUIAgent] Parsed Predictions:", JSON.stringify(parsedPredictions));
        if (!prediction) {
          logger2.error("[GUIAgent] Response Empty:", prediction);
          continue;
        }
        const predictionSummary = getSummary(prediction);
        end = Date.now();
        data2.conversations.push({
          from: "gpt",
          value: predictionSummary,
          timing: {
            start,
            end,
            cost: end - start
          },
          screenshotContext: {
            size: {
              width,
              height
            },
            scaleFactor: snapshot.scaleFactor
          },

// ---- main.js 第 122238–122470 行 ----
class NutJSOperator extends Operator$1 {
  async screenshot() {
    const { logger: logger2 } = useContext();
    const grabImage = await distExports$2.screen.grab();
    const screenWithScale = await grabImage.toRGB();
    const scaleFactor = screenWithScale.pixelDensity.scaleX;
    logger2.info("[NutjsOperator]", "scaleX", screenWithScale.pixelDensity.scaleX, "scaleY", screenWithScale.pixelDensity.scaleY);
    const screenWithScaleImage = await Jimp$1.fromBitmap({
      width: screenWithScale.width,
      height: screenWithScale.height,
      data: Buffer.from(screenWithScale.data)
    });
    const width = screenWithScale.width / screenWithScale.pixelDensity.scaleX;
    const height = screenWithScale.height / screenWithScale.pixelDensity.scaleY;
    const physicalScreenImage = await screenWithScaleImage.resize({
      w: width,
      h: height
    }).getBuffer("image/png");
    const output = {
      base64: physicalScreenImage.toString("base64"),
      scaleFactor
    };
    null == logger2 || logger2.info(`[NutjsOperator] screenshot: ${width}x${height}, scaleFactor: ${scaleFactor}`);
    return output;
  }
  async execute(params) {
    const { logger: logger2 } = useContext();
    const { parsedPrediction, screenWidth, screenHeight, scaleFactor } = params;
    const { action_type, action_inputs } = parsedPrediction;
    const startBoxStr = (null == action_inputs ? void 0 : action_inputs.start_box) || "";
    logger2.info("[NutjsOperator] execute", scaleFactor);
    const { x: startX, y: startY } = parseBoxToScreenCoords({
      boxStr: startBoxStr,
      screenWidth,
      screenHeight
    });
    logger2.info(`[NutjsOperator Position]: (${startX}, ${startY})`);
    distExports$2.mouse.config.mouseSpeed = 3600;
    const getHotkeys = (keyStr) => {
      if (keyStr) {
        const platformCommandKey = "darwin" === process.platform ? distExports$2.Key.LeftCmd : distExports$2.Key.LeftWin;
        const platformCtrlKey = "darwin" === process.platform ? distExports$2.Key.LeftCmd : distExports$2.Key.LeftControl;
        const keyMap = {
          return: distExports$2.Key.Enter,
          ctrl: platformCtrlKey,
          shift: distExports$2.Key.LeftShift,
          alt: distExports$2.Key.LeftAlt,
          "page down": distExports$2.Key.PageDown,
          "page up": distExports$2.Key.PageUp,
          meta: platformCommandKey,
          win: platformCommandKey,
          command: platformCommandKey,
          cmd: platformCommandKey,
          ",": distExports$2.Key.Comma,
          arrowup: distExports$2.Key.Up,
          arrowdown: distExports$2.Key.Down,
          arrowleft: distExports$2.Key.Left,
          arrowright: distExports$2.Key.Right
        };
        const lowercaseKeyMap = Object.fromEntries(Object.entries(distExports$2.Key).map(([k2, v2]) => [
          k2.toLowerCase(),
          v2
        ]));
        const keys = keyStr.split(/[\s+]/).map((k2) => k2.toLowerCase()).map((k2) => keyMap[k2] ?? lowercaseKeyMap[k2]).filter(Boolean);
        logger2.info("[NutjsOperator] hotkey: ", keys);
        return keys;
      }
      logger2.error("[NutjsOperator] hotkey error: ", `${keyStr} is not a valid key`);
      return [];
    };
    switch (action_type) {
      case "wait":
        logger2.info("[NutjsOperator] wait", action_inputs);
        await distExports$2.sleep(5e3);
        break;
      case "mouse_move":
      case "hover":
        logger2.info("[NutjsOperator] mouse_move");
        await moveStraightTo(startX, startY);
        break;
      case "click":
      case "left_click":
      case "left_single":
        logger2.info("[NutjsOperator] left_click");
        await moveStraightTo(startX, startY);
        await distExports$2.sleep(100);
        await distExports$2.mouse.click(distExports$2.Button.LEFT);
        break;
      case "left_double":
      case "double_click":
        logger2.info(`[NutjsOperator] ${action_type}(${startX}, ${startY})`);
        await moveStraightTo(startX, startY);
        await distExports$2.sleep(100);
        await distExports$2.mouse.doubleClick(distExports$2.Button.LEFT);
        break;
      case "right_click":
      case "right_single":
        logger2.info("[NutjsOperator] right_click");
        await moveStraightTo(startX, startY);
        await distExports$2.sleep(100);
        await distExports$2.mouse.click(distExports$2.Button.RIGHT);
        break;
      case "middle_click":
        logger2.info("[NutjsOperator] middle_click");
        await moveStraightTo(startX, startY);
        await distExports$2.mouse.click(distExports$2.Button.MIDDLE);
        break;
      case "left_click_drag":
      case "drag":
      case "select":
        if (null == action_inputs ? void 0 : action_inputs.end_box) {
          const { x: endX, y: endY } = parseBoxToScreenCoords({
            boxStr: action_inputs.end_box,
            screenWidth,
            screenHeight
          });
          if (startX && startY && endX && endY) {
            logger2.info(`[NutjsOperator] drag coordinates: startX=${startX}, startY=${startY}, endX=${endX}, endY=${endY}`);
            await moveStraightTo(startX, startY);
            await distExports$2.sleep(100);
            await distExports$2.mouse.drag(distExports$2.straightTo(new distExports$2.Point(endX, endY)));
          }
        }
        break;
      case "type": {
        var _action_inputs_content;
        const content = null == (_action_inputs_content = action_inputs.content) ? void 0 : _action_inputs_content.trim();
        logger2.info("[NutjsOperator] type", content);
        if (content) {
          const stripContent = content.replace(/\\n$/, "").replace(/\n$/, "");
          distExports$2.keyboard.config.autoDelayMs = 0;
          if ("win32" === process.platform) {
            const originalClipboard = await distExports$2.clipboard.getContent();
            await distExports$2.clipboard.setContent(stripContent);
            await distExports$2.keyboard.pressKey(distExports$2.Key.LeftControl, distExports$2.Key.V);
            await distExports$2.sleep(50);
            await distExports$2.keyboard.releaseKey(distExports$2.Key.LeftControl, distExports$2.Key.V);
            await distExports$2.sleep(50);
            await distExports$2.clipboard.setContent(originalClipboard);
          } else await distExports$2.keyboard.type(stripContent);
          if (content.endsWith("\n") || content.endsWith("\\n")) {
            await distExports$2.keyboard.pressKey(distExports$2.Key.Enter);
            await distExports$2.keyboard.releaseKey(distExports$2.Key.Enter);
          }
          distExports$2.keyboard.config.autoDelayMs = 500;
        }
        break;
      }
      case "hotkey": {
        const keyStr = (null == action_inputs ? void 0 : action_inputs.key) || (null == action_inputs ? void 0 : action_inputs.hotkey);
        const keys = getHotkeys(keyStr);
        if (keys.length > 0) {
          await distExports$2.keyboard.pressKey(...keys);
          await distExports$2.keyboard.releaseKey(...keys);
        }
        break;
      }
      case "press": {
        const keyStr = (null == action_inputs ? void 0 : action_inputs.key) || (null == action_inputs ? void 0 : action_inputs.hotkey);
        const keys = getHotkeys(keyStr);
        if (keys.length > 0) await distExports$2.keyboard.pressKey(...keys);
        break;
      }
      case "release": {
        const keyStr = (null == action_inputs ? void 0 : action_inputs.key) || (null == action_inputs ? void 0 : action_inputs.hotkey);
        const keys = getHotkeys(keyStr);
        if (keys.length > 0) await distExports$2.keyboard.releaseKey(...keys);
        break;
      }
      case "scroll": {
        const { direction } = action_inputs;
        if (null !== startX && null !== startY) await moveStraightTo(startX, startY);
        switch (null == direction ? void 0 : direction.toLowerCase()) {
          case "up":
            await distExports$2.mouse.scrollUp(500);
            break;
          case "down":
            await distExports$2.mouse.scrollDown(500);
            break;
          default:
            console.warn(`[NutjsOperator] Unsupported scroll direction: ${direction}`);
        }
        break;
      }
      case "error_env":
      case "call_user":
      case "finished":
      case "user_stop":
        return {
          status: agent_StatusEnum.END
        };
      default:
        logger2.warn(`Unsupported action: ${action_type}`);
    }
  }
}
_define_property$6(NutJSOperator, "MANUAL", {
  ACTION_SPACES: [
    "click(start_box='[x1, y1, x2, y2]')",
    "left_double(start_box='[x1, y1, x2, y2]')",
    "right_single(start_box='[x1, y1, x2, y2]')",
    "drag(start_box='[x1, y1, x2, y2]', end_box='[x3, y3, x4, y4]')",
    "hotkey(key='')",
    "type(content='') #If you want to submit your input, use \"\\n\" at the end of `content`.",
    "scroll(start_box='[x1, y1, x2, y2]', direction='down or up or right or left')",
    "wait() #Sleep for 5s and take a screenshot to check for any changes.",
    "finished()",
    "call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help."
  ]
});
class NutJSElectronOperator extends NutJSOperator {
  static MANUAL = {
    ACTION_SPACES: [
      `click(start_box='[x1, y1, x2, y2]')`,
      `left_double(start_box='[x1, y1, x2, y2]')`,
      `right_single(start_box='[x1, y1, x2, y2]')`,
      `drag(start_box='[x1, y1, x2, y2]', end_box='[x3, y3, x4, y4]')`,
      `hotkey(key='')`,
      `type(content='') #If you want to submit your input, use "\\n" at the end of \`content\`.`,
      `scroll(start_box='[x1, y1, x2, y2]', direction='down or up or right or left')`,
      `wait() #Sleep for 5s and take a screenshot to check for any changes.`,
      `finished()`,
      `call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.`
    ]
  };
  async screenshot() {
    const {
      physicalSize,
      logicalSize,
      scaleFactor,
      id: primaryDisplayId
    } = getScreenSize();
    logger.info(

// ---- main.js 第 184815–184845 行 ----
const getSystemPromptV1_5 = (language, useCase) => `You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format
\`\`\`
Thought: ...
Action: ...
\`\`\`

## Action Space

click(start_box='<|box_start|>(x1,y1)<|box_end|>')
left_double(start_box='<|box_start|>(x1,y1)<|box_end|>')
right_single(start_box='<|box_start|>(x1,y1)<|box_end|>')
drag(start_box='<|box_start|>(x1,y1)<|box_end|>', end_box='<|box_start|>(x3,y3)<|box_end|>')
hotkey(key='ctrl c') # Split keys with a space and use lowercase. Also, do not use more than 3 keys in one hotkey action.
type(content='xxx') # Use escape characters \\', \\", and \\n in content part to ensure we can parse the content in normal python string format. If you want to submit your input, use \\n at the end of content.
scroll(start_box='<|box_start|>(x1,y1)<|box_end|>', direction='down or up or right or left') # Show more information on the \`direction\` side.
wait() # Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.


## Note
- Use ${language === "zh" ? "Chinese" : "English"} in \`Thought\` part.
- ${"Generate a well-defined and practical strategy in the `Thought` section, summarizing your next move and its objective."}

## User Instruction
`;
const getSystemPromptDoubao_15_15B = (language) => `
You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.


// ---- main.js 第 99831–99960 行（循环后半：逐个执行动作、结束判断、每轮间隔）----
          predictionParsed: parsedPredictions
        });
        await (null == onData ? void 0 : onData({
          data: {
            ...data2,
            conversations: data2.conversations.slice(-1)
          }
        }));
        for (const parsedPrediction of parsedPredictions) {
          const actionType = parsedPrediction.action_type;
          logger2.info("[GUIAgent] Action:", actionType);
          if (actionType === constants_INTERNAL_ACTION_SPACES_ENUM.ERROR_ENV) {
            Object.assign(data2, {
              status: agent_StatusEnum.ERROR,
              error: this.guiAgentErrorParser(agent_ErrorStatusEnum.ENVIRONMENT_ERROR)
            });
            break;
          }
          if (actionType === constants_INTERNAL_ACTION_SPACES_ENUM.MAX_LOOP) {
            Object.assign(data2, {
              status: agent_StatusEnum.ERROR,
              error: this.guiAgentErrorParser(agent_ErrorStatusEnum.REACH_MAXLOOP_ERROR)
            });
            break;
          }
          if (!(null == signal ? void 0 : signal.aborted) && !this.isStopped) {
            var _retry_execute, _retry_execute1;
            logger2.info("[GUIAgent] Action Inputs:", parsedPrediction.action_inputs, parsedPrediction.action_type);
            const executeOutput = await async_retry(() => operator.execute({
              prediction,
              parsedPrediction,
              screenWidth: width,
              screenHeight: height,
              scaleFactor: snapshot.scaleFactor,
              factors: this.model.factors
            }), {
              retries: (null == retry2 ? void 0 : null == (_retry_execute = retry2.execute) ? void 0 : _retry_execute.maxRetries) ?? 0,
              minTimeout: 5e3,
              onRetry: null == retry2 ? void 0 : null == (_retry_execute1 = retry2.execute) ? void 0 : _retry_execute1.onRetry
            }).catch((e2) => {
              logger2.error("[GUIAgent] execute error", e2);
              Object.assign(data2, {
                status: agent_StatusEnum.ERROR,
                error: this.guiAgentErrorParser(agent_ErrorStatusEnum.EXECUTE_RETRY_ERROR, e2)
              });
            });
            if (executeOutput && (null == executeOutput ? void 0 : executeOutput.status)) data2.status = executeOutput.status;
          }
          if (actionType === constants_INTERNAL_ACTION_SPACES_ENUM.CALL_USER) {
            data2.status = agent_StatusEnum.CALL_USER;
            break;
          }
          if (actionType === constants_INTERNAL_ACTION_SPACES_ENUM.FINISHED) {
            data2.status = agent_StatusEnum.END;
            break;
          }
        }
        if (this.config.loopIntervalInMs && this.config.loopIntervalInMs > 0) {
          logger2.info(`[GUIAgent] sleep for ${this.config.loopIntervalInMs}ms before next loop`);
          await sleep$2(this.config.loopIntervalInMs);
          logger2.info(`[GUIAgent] sleep for ${this.config.loopIntervalInMs}ms before next loop done`);
        }
      }
    } catch (error2) {
      var _error_message;
      logger2.error("[GUIAgent] Catch error", error2);
      if (error2 instanceof Error && ("AbortError" === error2.name || (null == (_error_message = error2.message) ? void 0 : _error_message.includes("aborted")))) {
        logger2.info("[GUIAgent] Catch: request was aborted");
        data2.status = agent_StatusEnum.USER_STOPPED;
        return;
      }
      data2.status = agent_StatusEnum.ERROR;
      data2.error = this.guiAgentErrorParser(agent_ErrorStatusEnum.UNKNOWN_ERROR, error2);
    } finally {
      var _this_model;
      logger2.info("[GUIAgent] Finally: status", data2.status);
      null == (_this_model = this.model) || _this_model.reset();
      if (data2.status === agent_StatusEnum.USER_STOPPED) await operator.execute({
        prediction: "",
        parsedPrediction: {
          action_inputs: {},
          reflection: null,
          action_type: "user_stop",
          thought: ""
        },
        screenWidth: 0,
        screenHeight: 0,
        scaleFactor: 1,
        factors: [
          0,
          0
        ]
      });
      await (null == onData ? void 0 : onData({
        data: {
          ...data2,
          conversations: []
        }
      }));
      if (data2.status === agent_StatusEnum.ERROR) null == onError || onError({
        data: data2,
        error: data2.error || new GUIAgentError(agent_ErrorStatusEnum.UNKNOWN_ERROR, "Unknown error occurred")
      });
      logger2.info(`[GUIAgent] >>> totalTokens: ${totalTokens}, totalTime: ${totalTime}, loopCnt: ${loopCnt} <<<`);
    }
  }
  pause() {
    this.isPaused = true;
    this.resumePromise = new Promise((resolve2) => {
      this.resolveResume = resolve2;
    });
  }
  resume() {
    if (this.resolveResume) {
      this.resolveResume();
      this.resumePromise = null;
      this.resolveResume = null;
    }
    this.isPaused = false;
  }
  stop() {
    this.isStopped = true;
  }
  buildSystemPrompt() {
    var _this_operator_constructor_MANUAL, _this_operator_constructor;
    const actionSpaces = null == (_this_operator_constructor = this.operator.constructor) ? void 0 : null == (_this_operator_constructor_MANUAL = _this_operator_constructor.MANUAL) ? void 0 : _this_operator_constructor_MANUAL.ACTION_SPACES;
    return null == actionSpaces || 0 === actionSpaces.length ? SYSTEM_PROMPT : SYSTEM_PROMPT_TEMPLATE.replace("{{action_spaces_holder}}", actionSpaces.join("\n"));
  }
  guiAgentErrorParser(type2, error2) {
    this.logger.error("[GUIAgent] guiAgentErrorParser:", error2);
