import pandas as pd
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    StoppingCriteria,
    StoppingCriteriaList,
    TextIteratorStreamer,
    AutoConfig
)
import torch 
import re
import os
from tqdm import tqdm

df = pd.read_csv("./MTT/annotations_final.csv", sep="\t")

model_name = "mosaicml/mpt-7b-chat"
config = AutoConfig.from_pretrained(model_name, trust_remote_code=True)
config.attn_config['attn_impl'] = 'torch'
config.init_device = 'cuda' # For fast initialization directly on GPU!
model = AutoModelForCausalLM.from_pretrained(model_name, config=config, trust_remote_code=True, torch_dtype=torch.bfloat16)
tokenizer = AutoTokenizer.from_pretrained(model_name)

stop_token_ids = tokenizer.convert_tokens_to_ids(["<|im_end|>", "<|endoftext|>"])
start_message = """<|im_start|>system
- You are given a list of tags describing an audio
- You will give answers from the audio to these questions based on the list of tags
    1. Describe the music
    2. Describe the music in detail
    3. What do you hear in the audio
    4. What can be inferred from the audio
- The answers should be numbered <|im_end|>
"""

start_message_2 = """<|im_start|>system
- You are given a list of tags describing an audio
- You will create 5 questions related to the audio based on the list of tags along with answers
- The question answers should be long form
- The question answers should be numbered <|im_end|>
"""

class StopOnTokens(StoppingCriteria):
    def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor, **kwargs) -> bool:
        for stop_id in stop_token_ids:
            if input_ids[0][-1] == stop_id:
                return True
        return False

def convert_history_to_text(history, sm=start_message):
    text = sm + "".join(
        [
            "".join(
                [
                    f"<|im_start|>user\n{item[0]}<|im_end|>",
                    f"<|im_start|>assistant\n{item[1]}<|im_end|>",
                ]
            )
            for item in history[:-1]
        ]
    )
    text += "".join(
        [
            "".join(
                [
                    f"<|im_start|>user\n{history[-1][0]}<|im_end|>",
                    f"<|im_start|>assistant\n{history[-1][1]}",
                ]
            )
        ]
    )
    return text

def bot(history, temperature=0.5, top_p=1, top_k=4, repetition_penalty=1):
    while True:
        stop = StopOnTokens()

        # Construct the input message string for the model by concatenating the current system message and conversation history
        messages = convert_history_to_text(history)

        # Tokenize the messages string
        input_ids = tokenizer(messages, return_tensors="pt").input_ids
        input_ids = input_ids.to(model.device)
        generate_kwargs = dict(
            input_ids=input_ids,
            max_new_tokens=8192,
            temperature=temperature,
            do_sample=temperature > 0.0,
            top_p=top_p,
            top_k=top_k,
            repetition_penalty=repetition_penalty,
            stopping_criteria=StoppingCriteriaList([stop]),
        )

        full_history = tokenizer.batch_decode(model.generate(**generate_kwargs), skip_special_tokens=True)[0]
        match = re.search(r"assistant\n(?:\d\. (.*))\n(?:\d\. (.*))\n(?:\d\. (.*))\n(?:\d\. (.*))", full_history)
        if match is None:
            print("Retyring...")
            print(full_history)
            continue
        return {"Describe the audio": match.group(1), "Describe the audio in detail": match.group(2), 
                "What do you hear in the audio?":match.group(3), "What can be inferred from the audio?":match.group(4)}

def open_bot(history, temperature=0.4, top_p=1, top_k=4, repetition_penalty=1):
    while True:
        stop = StopOnTokens()

        # Construct the input message string for the model by concatenating the current system message and conversation history
        messages = convert_history_to_text(history, sm=start_message_2)

        # Tokenize the messages string
        input_ids = tokenizer(messages, return_tensors="pt").input_ids
        input_ids = input_ids.to(model.device)
        generate_kwargs = dict(
            input_ids=input_ids,
            max_new_tokens=8192,
            temperature=temperature,
            do_sample=temperature > 0.0,
            top_p=top_p,
            top_k=top_k,
            repetition_penalty=repetition_penalty,
            stopping_criteria=StoppingCriteriaList([stop]),
        )

        full_history = tokenizer.batch_decode(model.generate(**generate_kwargs), skip_special_tokens=True)[0]
        match = re.search(r"assistant\n(?:\d\. (.*)\nAnswer: (.*))\n(?:\d\. (.*)\nAnswer: (.*))\n(?:\d\. (.*)\nAnswer: (.*))\n(?:\d\. (.*)\nAnswer: (.*))\n(?:\d\. (.*)\nAnswer: (.*))", full_history)
        if match is None:
            print("Retyring...")
            print(full_history)
            continue
        generated = {}
        for qid in range(1, 11, 2):
            generated[match.group(qid)] = match.group(qid+1)
        if len(generated.keys()) != 5:
            continue
        return generated
    
    
def get_qa(caption):
    return bot([[caption, ""]])

def get_open_qa(caption):
    return open_bot([[caption, ""]])


if os.path.exists("./MTT/MTT_AQA.csv"):
    df_qa = pd.read_csv("./MTT/MTT_AQA.csv", sep=";")
    filename_set = set(df_qa["audio_name"].values.tolist())
    data = df_qa.to_dict(orient='list')
    del data['Unnamed: 0']
else:
    data = {"audio_name": [], "Describe the audio": [], "Describe the audio in detail": [], "What do you hear in the audio?":[], "What can be inferred from the audio?":[],
           "OpenQA1": [], "OpenQA2": [], "OpenQA3": [], "OpenQA4": [], "OpenQA5": []} 
    filename_set = set()
    

indices = []

for i, row in tqdm(df.iterrows(), total=len(df)):
    a = row[1:-1]
    tags = a[a == 1].index
    if len(tags) >= 5:
         indices.append(i)

df = df[df.index.isin(indices)].reset_index()

count = 0

for i, row in tqdm(df.iterrows(), total=len(df)):
    try:
        a = row[1:-1]
        tags = a[a == 1].index
        if len(tags) < 5:
            continue
        tags = ", ".join(tags)
        filename = row[-1].split('/')[-1]
        if filename in filename_set:
            continue
        qa = get_qa(tags)
        for q, a in qa.items():
            data[q].append(a)
        qa = get_open_qa(tags)
        for i, (q, a) in enumerate(qa.items()):
            data[f"OpenQA{i+1}"].append(f"Q:{q}\tA:{a}")
        count += 1
        data["audio_name"].append(filename)
        if count % 10 == 0:
            df_qa = pd.DataFrame(data)
            df_qa.to_csv("./MTT/MTT_AQA.csv", sep=";")
    except:
        continue

df_qa = pd.DataFrame(data)
df_qa.to_csv("./MTT/MTT_AQA.csv", sep=";")