---
dataset_info:
  features:
  - name: instruction
    dtype: string
  - name: input
    dtype: string
  - name: output
    dtype: string
  - name: alternative_output
    dtype: string
  - name: label
    dtype: string
  - name: all_labels
    sequence: string
  - name: agreement
    dtype: float32
  - name: overlap
    dtype: uint32
  splits:
  - name: train
    num_bytes: 54774775
    num_examples: 29822
  download_size: 14565995
  dataset_size: 54774775
license: cc-by-4.0
task_categories:
- text-generation
- text2text-generation
language:
- ru
tags:
- instruction-finetuning
- instruction generation
- alpaca
size_categories:
- 10K<n<100K
---
# RuTurboAlpaca

Dataset of ChatGPT-generated instructions in Russian.


<img src="https://cdn.midjourney.com/770a35fa-00c0-4214-bb88-727dbc7cfaf3/0_0.png" >

* Code: [rulm/self_instruct](https://github.com/IlyaGusev/rulm/tree/master/self_instruct)
* Code is based on [Stanford Alpaca](https://github.com/tatsu-lab/stanford_alpaca) and [self-instruct](https://github.com/yizhongw/self-instruct/).
* 29822 examples

Preliminary evaluation by an expert based on 400 samples:
* 83% of samples contain correct instructions
* 63% of samples have correct instructions and outputs

Crowdsouring-based evaluation on 3500 samples:
* 90% of samples contain correct instructions
* 68% of samples have correct instructions and outputs

Prompt template:
```
Составь набор из {{num_tasks}} разных заданий для дообучения языковой модели:

1. Делай задания максимально непохожими друг на друга: по типу, по запрашиваемым действиям, по формулировке, по наличию входа.
2. Задания должны быть выполнимы языковой моделью, которая не умеет работать с картинками, видео, и аудио, и не имеет доступа ко внешнему миру.
3. Используй хороший грамотный русский язык.
4. Делай задания в одно или два предложения.
5. Генерируй подходящие реалистичные входные данные, не используй общие шаблоны типа \"Имя человека\" или [имя] вместо реального имени.
6. Задание может быть без входных данных, в таком случае используй токен <noinput> вместо них.
7. На выходе сгенерируй подходящий длинный ответ.
8. Следуй тому же шаблону, который приведен в примерах, разделяй задания с помощью ###. Это важно!

Примеры заданий:
{% for task in example_tasks %}
{{task.index}}. Задание: {{task.instruction}}
{{task.index}}. Вход: {{task.input}}
{{task.index}}. Выход: {{task.output}}

{{ "###" if not loop.last else "" }}
{% endfor %}
```


## Legal disclaimer

Data is based on OpenAI’s gpt-3.5-turbo, whose [terms of use](https://openai.com/policies/terms-of-use) prohibit for us developing models that compete with OpenAI. Not for you.