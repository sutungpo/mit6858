import symex.fuzzy as fuzzy

def make_a_test_case():
  concrete_values = fuzzy.ConcreteValues()
  ## Your solution here: add the right value to concrete_values
  concrete_values.add('i', 862)
  concrete_values.mk_global()
  return concrete_values
